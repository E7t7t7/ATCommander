"""In-app Discord connection, durable event timers and Tk-thread request dispatch."""
from datetime import datetime,timedelta
import importlib.util
import json
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk,messagebox
import webbrowser
from .siege_orders_ui import SiegeOrdersMixin
from copy import deepcopy
from .remote_control import EventTimers,RemoteInbox,MODES,DAYS,remote_command
from .remote_discord import DiscordConnection
from .command_center import CommandCenterMixin
from .protocol_config import load_profiles
from .cm_discord_bot import TOKEN_SERVICE,TOKEN_USER
from .log_view import logbox,split_controls_log

class RemoteCenterMixin(SiegeOrdersMixin):
    def _build_remote_center(self):
        self.discord_connection=None;self.remote_job=None;self.event_timers=EventTimers(self.command_schema.store)
        config=self.command_schema.store.read().get('discord_remote',{})
        if not isinstance(config,dict):config={}
        self.discord_owner=tk.StringVar(value=str(config.get('owner','')));self.discord_token=tk.StringVar()
        self.discord_autoconnect=tk.BooleanVar(value=config.get('autoconnect')is True)
        self.discord_state=tk.StringVar(value='Disconnected');self.discord_application='';self.timer_vars={}
        self.remote_panes,controls,activity=split_controls_log(self.remote_tab,controls_height=510)
        head=ttk.Frame(controls,padding=12);head.pack(fill='x')
        ttk.Label(head,text='Your phone, connected to Commander',style='Big.TLabel').pack(anchor='w')
        ttk.Label(head,text='Leave this PC awake and Commander open. Timers use the independent event defaults below. Only your Discord user ID can control it.',wraplength=1060).pack(anchor='w',pady=(4,10))
        form=ttk.LabelFrame(head,text='Discord connection',padding=10);form.pack(fill='x')
        for row,(text,variable,secret)in enumerate([('Your Discord user ID',self.discord_owner,False),('Bot token (saved securely)',self.discord_token,True)]):
            ttk.Label(form,text=text).grid(row=row,column=0,sticky='w',pady=3)
            ttk.Entry(form,textvariable=variable,show='*'if secret else '',width=64).grid(row=row,column=1,columnspan=4,sticky='ew',padx=10,pady=3)
        form.columnconfigure(1,weight=1)
        buttons=ttk.Frame(form);buttons.grid(row=2,column=0,columnspan=5,sticky='w',pady=6)
        for text,command in [('Save connection',self._remote_save),('Connect',self._remote_connect),('Disconnect',self._remote_disconnect),('Invite bot',self._remote_invite),('Open bot DM',self._remote_open_dm),('Setup guide',self._remote_help)]:ttk.Button(buttons,text=text,command=command).pack(side='left',padx=(0,6))
        ttk.Checkbutton(form,text='Connect automatically when Commander opens',variable=self.discord_autoconnect).grid(row=3,column=0,columnspan=4,sticky='w')
        self.discord_install=ttk.Button(form,text='Install Discord support',command=self._remote_install);self.discord_install.grid(row=4,column=0,sticky='w',pady=6)
        ttk.Label(form,textvariable=self.discord_state,wraplength=850).grid(row=4,column=1,columnspan=4,sticky='w',padx=10)
        timers=ttk.LabelFrame(head,text='Automatic event checks',padding=10);timers.pack(fill='x',pady=(12,0))
        for column,text in enumerate(('Event / day','Every (minutes)','First run HH:MM (optional)','Next check / state','')):ttk.Label(timers,text=text).grid(row=0,column=column,sticky='w',padx=5)
        for row,mode in enumerate(MODES,1):
            minutes=tk.StringVar(value=str(self.event_timers.rows[mode]['minutes']));first=tk.StringVar();state=tk.StringVar()
            day=tk.StringVar(value=DAYS[self.event_timers.rows[mode]['weekday']+1]if self.event_timers.rows[mode]['weekday']is not None else DAYS[0])
            self.timer_vars[mode]=(minutes,first,state,day)
            daybox=ttk.Frame(timers);daybox.grid(row=row,column=0,sticky='w',padx=5)
            ttk.Label(daybox,text=mode).pack(anchor='w')
            ttk.Combobox(daybox,textvariable=day,values=DAYS,state='readonly',width=11).pack(anchor='w')
            ttk.Spinbox(timers,textvariable=minutes,from_=1,to=1440,width=7).grid(row=row,column=1,sticky='w',padx=5,pady=4)
            ttk.Entry(timers,textvariable=first,width=9).grid(row=row,column=2,sticky='w',padx=5)
            ttk.Label(timers,textvariable=state,wraplength=360).grid(row=row,column=3,sticky='w',padx=5)
            actions=ttk.Frame(timers);actions.grid(row=row,column=4,sticky='e')
            ttk.Button(actions,text='Arm',command=lambda m=mode:self._timer_arm_ui(m)).pack(side='left',padx=3)
            ttk.Button(actions,text='Disarm',command=lambda m=mode:self._timer_disarm(m)).pack(side='left')
        timers.columnconfigure(3,weight=1)
        ttk.Label(head,text='Siege defaults to Mondays. Choose any weekday or Every day. Blank time starts checking on the next allowed day; HH:MM uses PC local time. Busy checks wait; errors pause the timer.',wraplength=1080).pack(anchor='w',pady=8)
        ttk.Label(head,text='Phone: /commander status · /commander run · /commander timer · /commander command · /commander stop',wraplength=1080).pack(anchor='w')
        self._build_event_defaults(head)
        self.remote_log=logbox(activity,height=7,pady=(0,6))
        self._timers_render()
        if self.discord_autoconnect.get():self.root.after(2000,self._remote_connect)

    def _remote_save(self):
        try:
            owner=self.discord_owner.get().strip();RemoteInbox(owner)
            token=self.discord_token.get().strip()
            current=self.discord_connection
            if current and (current.inbox.owner_id!=int(owner) or token):current.close()
            if token:
                import keyring
                keyring.set_password(TOKEN_SERVICE,TOKEN_USER,token);self.discord_token.set('')
            self.command_schema.store.update('discord_remote',{'owner':owner,'autoconnect':bool(self.discord_autoconnect.get())})
            self.discord_state.set('Saved. Connect to enable phone commands.');return True
        except Exception:
            self.discord_token.set('');self.discord_state.set('Could not save. Check your Discord user ID and Windows credential storage.');return False
    def _remote_connect(self):
        if getattr(self,'_closing',False):return
        old=self.discord_connection
        if old and old.thread and old.thread.is_alive():
            self.discord_state.set('The previous connection is still open or stopping.');return
        if importlib.util.find_spec('discord')is None:
            self.discord_state.set('Install Discord support first.');return
        if not self._remote_save():return
        inbox=RemoteInbox(self.discord_owner.get().strip());self.discord_connection=DiscordConnection(inbox,self.events.put,orders_config=self.guild_config)
        self.discord_connection.start();self.discord_state.set('Connecting…')
    def _remote_disconnect(self):
        if self.discord_connection:self.discord_connection.close()
        self.discord_state.set('Disconnected. Event timers still run locally.')
    def _remote_install(self):
        if getattr(self,'discord_installing',False):return
        self.discord_installing=True;self.discord_install.configure(state='disabled');self.discord_state.set('Installing Discord support…')
        def work():
            try:
                result=subprocess.run([sys.executable,'-m','pip','install','--disable-pip-version-check','discord.py>=2.5,<3'],capture_output=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),timeout=180)
                ok=result.returncode==0
            except Exception:ok=False
            self.events.put({'kind':'discord_installed','ok':ok})
        threading.Thread(target=work,daemon=True,name='Commander Discord setup').start()
    def _remote_invite(self):
        if not self.discord_application:self.discord_state.set('Connect first so Commander can generate your bot invite.');return
        webbrowser.open('https://discord.com/oauth2/authorize?client_id='+self.discord_application+'&scope=bot%20applications.commands&permissions=2048')
    def _remote_open_dm(self):
        if not self.discord_application:self.discord_state.set('Connect first, then open the bot profile and choose Message.');return
        webbrowser.open('https://discord.com/users/'+self.discord_application)

    def _remote_help(self):
        messagebox.showinfo('Discord setup','1. Create a bot at discord.com/developers/applications.\n2. Copy its Bot token into this masked field.\n3. In Discord enable User Settings → Advanced → Developer Mode, then right-click your profile → Copy User ID. Paste it here.\n4. Save, install support if needed, then Connect and Invite bot to your server.\n5. On your phone use /commander help. Allow DMs from your bot for completion alerts.\n\nNo Message Content Intent is needed. Stop the separate CM bot if it uses the same bot token. Never paste your bot token into chat.')
        webbrowser.open('https://discord.com/developers/applications')
    def _timer_arm_ui(self,mode):
        try:
            minutes,first,_,day=self.timer_vars[mode];now=time.time();initial=None
            if first.get().strip():
                clock=datetime.strptime(first.get().strip(),'%H:%M');current=datetime.now();initial=current.replace(hour=clock.hour,minute=clock.minute,second=0,microsecond=0)
                if initial<=current:initial+=timedelta(days=1)
                initial=initial.timestamp()
            self.guild_suspended=False
            self.event_timers.arm(mode,int(minutes.get()),now,initial,weekday=DAYS.index(day.get())-1 if day.get()!=DAYS[0]else None);self._timers_render()
        except ValueError as error:messagebox.showinfo('Event timer',str(error))
    def _timer_disarm(self,mode):self.event_timers.disarm(mode);self._timers_render()
    def _timers_render(self):
        for mode,(_,_,state,_)in self.timer_vars.items():
            row=self.event_timers.rows[mode]
            text='Running'if row['active']else datetime.fromtimestamp(row['due']).strftime('%a %H:%M')if row['enabled']else'Disarmed'
            state.set(text+' · '+row['note'])
            self.timer_vars[mode][0].set(str(row['minutes']))
            self.timer_vars[mode][3].set(DAYS[row['weekday']+1]if row['weekday']is not None else DAYS[0])
    def _remote_status(self):
        active=[label for label,attr in [('Arena','arena_runner'),('Adventure','adventure_runner'),('Events','event_runner'),('Swole','swole_runner')]if getattr(getattr(self,attr,None),'running',False)]
        if self.command_busy:active.append('Command / routine')
        if getattr(self,'research_busy',False):active.append('Research')
        return 'Commander online. Active: '+(', '.join(active)or'idle')+'\n'+'\n'.join(mode+': '+('armed ('+(DAYS[row['weekday']+1]if row['weekday']is not None else 'Every day')+'), next '+datetime.fromtimestamp(row['due']).strftime('%a %H:%M')if row['enabled']else'disarmed')for mode,row in self.event_timers.rows.items())
    def _remote_begin_event(self,mode,scheduled=False):
        if mode not in MODES:raise ValueError('Choose Siege or Rumble.')
        if CommandCenterMixin._browser_action_busy(self):raise ValueError('Commander is busy. No event run was started.')
        if not getattr(self,'deck_rows',None):raise ValueError('Wait for Refresh account to load your saved deck selections first.')
        if mode=='Rumble'and not load_profiles().get('rumble'):raise ValueError('Set up the Rumble battle profile in Commander first.')
        defaults=deepcopy(self.event_defaults[mode])
        if mode=='Siege'and not self._orders_ready():raise ValueError('Fresh guild orders are not ready. Review the guild preview first.')
        if mode=='Siege'and not defaults.get('targets')and not defaults.get('fallback')and not self.guild_config.get('use_attack'):raise ValueError('Set Siege defaults or enable fresh guild attack priorities first.')
        guild_plan={'plan':deepcopy(self.guild_plan),'use_attack':self.guild_config.get('use_attack')is True,'use_defense':self.guild_config.get('use_defense')is True,'fallback':self.guild_config.get('fallback')is True}if mode=='Siege'else None
        for variable in (self.delay_var,self.battle_delay_var):float(variable.get())
        accepted=self.start_event(mode,scheduled=True,defaults=defaults,guild_plan=guild_plan)
        if accepted is not True:raise ValueError('The event run did not start. Review Events on your PC.')
        self.remote_job={'mode':mode,'scheduled':scheduled,'battles':0};return mode+' started using your saved timer/phone defaults.'
    def _remote_dispatch(self,request):
        if request.action=='help':return 'DM commands: status · siege · rumble · stop · timer siege Monday 30 · timer rumble daily 30 · command play-status · cm Wingnut Amy. Paste the full Sunday attack post or Calculon roster directly into this DM.\n\n/commander status\n/commander run event:Siege or Rumble\n/commander timer event:Siege enabled:true minutes:30\n/commander command name:play-status\n/commander stop\nRuns use saved PC settings; stop also disarms timers. Purchases and destructive commands require the PC.'
        if request.action=='orders':return self._orders_from_owner(request.target,request.arguments)
        if request.action=='status':return self._remote_status()
        if request.action=='stop':self.stop_all();return 'Stop requested. Event timers are disarmed; active workers are settling.'
        if request.action=='timer':
            if request.target not in MODES:raise ValueError('Choose Siege or Rumble.')
            if request.arguments=='off':self._timer_disarm(request.target);return request.target+' timer disarmed.'
            options=json.loads(request.arguments)if request.arguments.startswith('{')else {'minutes':int(request.arguments)}
            self.guild_suspended=False
            self.event_timers.arm(request.target,options['minutes'],time.time(),weekday=options.get('weekday','saved'));self._timers_render()
            return request.target+' timer armed. Next check: '+datetime.fromtimestamp(self.event_timers.rows[request.target]['due']).strftime('%a %H:%M')
        if request.action=='run':return self._remote_begin_event(request.target)
        if request.action=='command':
            if CommandCenterMixin._browser_action_busy(self):raise ValueError('Commander is busy. No command was started.')
            name,args=remote_command(self.command_schema,request.target,request.arguments)
            if not self._command_launch([(name,args)],False):raise ValueError('This command could not start.')
            self.remote_command_name=name;return 'Started '+name+'. Completion will be sent by DM; full results stay in Command Center.'
        raise ValueError('Unknown action.')
    def _remote_tick(self):
        if getattr(self,'_closing',False):return
        connection=self.discord_connection
        if connection:
            for _ in range(4):
                try:request=connection.inbox.queue.get_nowait()
                except queue.Empty:break
                if not request.result.set_running_or_notify_cancel():continue
                try:answer=self._remote_dispatch(request)
                except (ValueError,TypeError):answer='Request could not run. Check its settings, permissions and whether Commander is busy.'
                except Exception:answer='Commander could not accept this request. Review its PC activity log before trying again.'
                request.result.set_result(answer)
        if hasattr(self,'guild_config'):self._guild_tick()
        if CommandCenterMixin._browser_action_busy(self):return
        if not getattr(self,'deck_rows',None):return
        due=self.event_timers.due(time.time())
        if not due:return
        mode=next((m for m in due if m!='Siege'or self._orders_ready()),None)
        if mode is None:return
        self.event_timers.claim(mode,time.time())
        try:self._remote_begin_event(mode,scheduled=True)
        except Exception:
            self.event_timers.finish(mode,failed=True);self._remote_notify(mode+' timer paused: settings or event startup need review in Commander.')
        self._timers_render()
    def _remote_notify(self,text):
        self._append(self.remote_log,text)
        if self.discord_connection:self.discord_connection.notify(text)
    def _remote_observe(self,event):
        if hasattr(self,"guild_config")and self._orders_event(event):return True
        kind=event.get('kind');job=self.remote_job
        if job and event.get('mode')==job['mode']:
            if kind=='battle_end':job['battles']+=1
            if kind in {'finished','fatal'}:
                if job['scheduled']:self.event_timers.finish(job['mode'],failed=kind=='fatal');self._timers_render()
                if kind=='fatal'or job['battles']:self._remote_notify(job['mode']+(' paused after an error; check Events before restarting.'if kind=='fatal'else f" completed: {job['battles']} battle(s)."))
                self.remote_job=None
        if kind=='command_result'and getattr(self,'remote_command_name',None):
            self._remote_notify(self.remote_command_name+(' completed. Results are in Commander.'if event.get('ok')else' stopped with an error; check Commander before retrying.'));self.remote_command_name=None
        if kind=='discord_status':
            if self.discord_connection and self.discord_connection.inbox.closed:return True
            self.discord_state.set(event['text']);self.discord_application=event.get('application_id',self.discord_application);return True
        if kind=='discord_notice_failed':self._append(self.remote_log,'Discord could not deliver a completion DM. Check your privacy settings.');return True
        if kind=='discord_installed':
            self.discord_installing=False;self.discord_install.configure(state='normal');self.discord_state.set('Discord support installed. You can Connect.'if event['ok']else'Installation failed. Check the PC connection and try again.');return True
        return False
    def _remote_stop_timers(self):
        if hasattr(self,'guild_defense_stop'):self.guild_defense_stop.set()
        if getattr(self,'_closing',False):return
        if hasattr(self,'guild_defense_stop'):self.guild_defense_stop.set();self.guild_suspended=True
        if hasattr(self,'event_timers'):
            for mode in MODES:self.event_timers.disarm(mode)
            self._timers_render()
