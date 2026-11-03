"""Saved event loadouts and guild-order preview controls inside Discord & timers."""
import time
from datetime import datetime
import threading
from .api import ATClient
from .credentials import load_credentials
from .command_center import CommandCenterMixin
from .guild_defense import apply_assigned_defense
import tkinter as tk
from tkinter import ttk,messagebox
import webbrowser
from .event_defaults import initial_defaults
from .deck_selection import label_parts,resolve_deck
from .guild_orders import GUILD_ID,ATTACK_CHANNEL,DEFENSE_CHANNEL,parse_orders

class SiegeOrdersMixin:
    def _build_event_defaults(self,parent):
        saved=self.command_schema.store.read();self.event_defaults=saved.get('scheduled_event_defaults',{})
        if not isinstance(self.event_defaults,dict):self.event_defaults={}
        self.default_vars={};self.default_widgets={}
        box=ttk.LabelFrame(parent,text='Saved defaults for scheduled and phone runs',padding=10);box.pack(fill='x',pady=10)
        ttk.Label(box,text='These defaults are independent of Events. Choose an offense deck and hero/commander for each event, then Save defaults.',wraplength=1000).pack(anchor='w')
        tabs=ttk.Notebook(box);tabs.pack(fill='x',pady=6)
        for mode in ('Siege','Rumble'):
            row=self.event_defaults.setdefault(mode,initial_defaults(self.settings,mode));page=ttk.Frame(tabs,padding=8);tabs.add(page,text=mode)
            variables={};widgets={}
            for index,(key,label)in enumerate((('offense_deck','Offense deck'),('offense_hero','Hero / commander'),('defense_deck','Defense deck'))):
                variable=tk.StringVar(value='Current / unchanged');variables[key]=variable
                ttk.Label(page,text=label).grid(row=index,column=0,sticky='w',padx=4,pady=3)
                widget=ttk.Combobox(page,textvariable=variable,state='readonly',width=47);widget.grid(row=index,column=1,sticky='ew',padx=4,pady=3);widgets[key]=widget
            variables['logic']=tk.StringVar(value=row.get('logic','Item → Combo (default)'))
            ttk.Label(page,text='Battle logic').grid(row=0,column=2,sticky='w',padx=8)
            ttk.Combobox(page,textvariable=variables['logic'],state='readonly',values=('Item → Combo (default)','Item → Combo (aggressive tiebreak)','Item → Combo (durable tiebreak)'),width=36).grid(row=0,column=3,sticky='ew')
            if mode=='Siege':
                for index,(key,label)in enumerate((('island','Defense island'),('target_a','Attack priority A'),('target_b','Attack priority B'),('target_c','Attack priority C')),1):
                    variable=tk.StringVar(value='Current / unchanged'if key=='island'else'Any / none');variables[key]=variable
                    ttk.Label(page,text=label).grid(row=index,column=2,sticky='w',padx=8)
                    widget=ttk.Combobox(page,textvariable=variable,state='readonly',width=36);widget.grid(row=index,column=3,sticky='ew',pady=3);widgets[key]=widget
                variables['fallback']=tk.BooleanVar(value=row.get('fallback',True)is True)
                ttk.Checkbutton(page,text='After A/B/C are down, allow any living target',variable=variables['fallback']).grid(row=4,column=0,columnspan=2,sticky='w')
            page.columnconfigure(1,weight=1);page.columnconfigure(3,weight=1)
            buttons=ttk.Frame(page);buttons.grid(row=5,column=0,columnspan=4,sticky='w',pady=5)
            ttk.Button(buttons,text='Save '+mode+' defaults',command=lambda m=mode:self._defaults_save(m)).pack(side='left')
            ttk.Button(buttons,text='Copy current Events selections',command=lambda m=mode:self._defaults_copy_events(m)).pack(side='left',padx=8)
            self.default_vars[mode]=variables;self.default_widgets[mode]=widgets
        self.command_schema.store.update('scheduled_event_defaults',self.event_defaults)
        self._defaults_refresh()
        config=saved.get('guild_sources',{});config=config if isinstance(config,dict)else{}
        self.guild_defense_thread=None;self.guild_defense_stop=threading.Event();self.guild_next_defense=0
        self.guild_config=dict(config);self.guild_plan={};self.guild_fetching=False;self.guild_retry_at=0;self.guild_next_read=time.time()+5
        self.guild_enabled=tk.BooleanVar(value=config.get('enabled')is True);self.guild_attack=tk.BooleanVar(value=config.get('use_attack')is True)
        self.guild_auto_defense=tk.BooleanVar(value=config.get('auto_defense')is True)
        self.guild_defense=tk.BooleanVar(value=config.get('use_defense')is True);self.guild_fallback=tk.BooleanVar(value=config.get('fallback')is True)
        self.guild_alias=tk.StringVar(value=str(config.get('alias','')));self.guild_leaders=tk.StringVar(value=str(config.get('leaders','')))
        self.guild_preview=tk.StringVar(value='No guild orders fetched yet. No channel messages are read until guild reading is enabled.')
        guild=ttk.LabelFrame(parent,text='Guild Siege orders',padding=10);guild.pack(fill='x',pady=8)
        ttk.Checkbutton(guild,text='Read the two configured guild channels (requires bot access + Message Content Intent)',variable=self.guild_enabled).pack(anchor='w')
        for text,cid in [('Attack announcements',ATTACK_CHANNEL),('Calculon defense assignments',DEFENSE_CHANNEL)]:
            ttk.Button(guild,text=text+' ↗',command=lambda c=cid:webbrowser.open(f'https://discord.com/channels/{GUILD_ID}/{c}')).pack(anchor='w',pady=2)
        for label,var in [('My assignment name / alias',self.guild_alias),('Attack leader Discord IDs (optional, comma separated)',self.guild_leaders)]:
            line=ttk.Frame(guild);line.pack(fill='x',pady=3);ttk.Label(line,text=label).pack(side='left');ttk.Entry(line,textvariable=var,width=44).pack(side='right')
        ttk.Checkbutton(guild,text='Use fresh guild attack priorities for scheduled / phone Siege runs',variable=self.guild_attack).pack(anchor='w')
        ttk.Checkbutton(guild,text='Use my Calculon defense assignment for Siege defaults',variable=self.guild_defense).pack(anchor='w')
        ttk.Checkbutton(guild,text='Automatically apply my Calculon defense assignment during Siege setup (before battle locks)',variable=self.guild_auto_defense).pack(anchor='w')
        ttk.Checkbutton(guild,text='If all guild targets fall, allow any living enemy island',variable=self.guild_fallback).pack(anchor='w')
        buttons=ttk.Frame(guild);buttons.pack(fill='x',pady=6)
        ttk.Button(buttons,text='Save guild sources',command=self._orders_save).pack(side='left')
        ttk.Button(buttons,text='Fetch / preview guild orders',command=self._orders_fetch).pack(side='left',padx=8)
        ttk.Button(buttons,text='Apply assigned defense now',command=lambda:self._guild_apply_defense(manual=True)).pack(side='left')
        ttk.Label(guild,textvariable=self.guild_preview,wraplength=1000).pack(anchor='w',pady=4)
        ttk.Label(guild,text='Reads only these two channels. Attack posts expire after 72 hours; defense assignments after 7 days. Ambiguous messages block automatic use. Raw posts are not saved. Save reading options, then reconnect Discord after enabling Message Content Intent in the Developer Portal.',wraplength=1000).pack(anchor='w')

    def _defaults_refresh(self):
        if not hasattr(self,'default_vars'):return
        def populate(widget,var,rows,identity,name='',default='Current / unchanged'):
            previous_id,previous_name=label_parts(var.get());chosen=resolve_deck(previous_id or identity,previous_name or name,rows)
            labels=[default]+self._display_options(rows)[1:]
            widget['values']=labels;var.set(next((label for label in labels if chosen and f'[ID {chosen}]'in label),default))
        for mode,variables in self.default_vars.items():
            row=self.event_defaults[mode];widgets=self.default_widgets[mode]
            for key in ('offense_deck','defense_deck','offense_hero'):
                rows=self.hero_rows if key=='offense_hero'else self.deck_rows
                populate(widgets[key],variables[key],rows,row.get(key+'_id'),row.get(key+'_name',''))
            if mode=='Siege':
                populate(widgets['island'],variables['island'],self.siege_island_rows,row.get('island_id'))
                for index,key in enumerate(('target_a','target_b','target_c')):
                    ids=row.get('targets',[]);populate(widgets[key],variables[key],self.siege_target_rows,ids[index]if len(ids)>index else'',default='Any / none')
        if hasattr(self,'guild_alias')and not self.guild_alias.get():
            name=(getattr(self,'current_snapshot',{}).get('user_data')or{}).get('name')
            if name:self.guild_alias.set(str(name))
    def _defaults_save(self,mode):
        variables=self.default_vars[mode];row={'logic':variables['logic'].get()}
        for key in ('offense_deck','offense_hero','defense_deck'):
            identity,name=label_parts(variables[key].get());row[key+'_id']=identity or'';row[key+'_name']=name or''
        row['island_id']=(label_parts(variables['island'].get())[0]or'')if'island'in variables else''
        row['targets']=[identity for key in ('target_a','target_b','target_c')if key in variables for identity in [label_parts(variables[key].get())[0]]if identity]
        row['fallback']=variables['fallback'].get()if'fallback'in variables else True
        self.event_defaults[mode]=row;self.command_schema.store.update('scheduled_event_defaults',self.event_defaults)
        self._append(self.remote_log,mode+' timer/phone defaults saved independently of Events.')
    def _defaults_copy_events(self,mode):
        prefix=mode.lower();mapping={'offense_deck':prefix+'_offense_deck','offense_hero':prefix+'_offense_hero','defense_deck':prefix+'_deck'}
        for key,attribute in mapping.items():self.default_vars[mode][key].set(getattr(self,attribute).get())
        self.default_vars[mode]['logic'].set(getattr(self,prefix+'_logic').get())
        if mode=='Siege':
            self.default_vars[mode]['island'].set(self.siege_island.get());self.default_vars[mode]['fallback'].set(self.siege_fallback_any.get())
            for key in ('target_a','target_b','target_c'):self.default_vars[mode][key].set(getattr(self,'siege_'+key).get())
        self._defaults_save(mode)
    def _orders_save(self):
        leaders=[x.strip()for x in self.guild_leaders.get().split(',')if x.strip()]
        if any(not x.isdigit()for x in leaders):self.guild_preview.set('Leader IDs must be numeric Discord IDs.');return False
        enabled=self.guild_enabled.get()or self.guild_attack.get()or self.guild_defense.get()or self.guild_auto_defense.get();self.guild_enabled.set(enabled)
        previous=self.guild_config.get('enabled')is True
        self.guild_config={'enabled':enabled,'use_attack':self.guild_attack.get(),'use_defense':self.guild_defense.get(),'auto_defense':self.guild_auto_defense.get(),'fallback':self.guild_fallback.get(),'alias':self.guild_alias.get().strip(),'leaders':','.join(leaders)}
        self.command_schema.store.update('guild_sources',self.guild_config);self.guild_retry_at=0;self.guild_next_read=0;self.guild_plan={};self.guild_suspended=False
        if previous!=enabled and self.discord_connection:self.discord_connection.close();self.guild_preview.set('Saved. Reconnect Discord with Message Content Intent enabled for guild reading.')
        else:self.guild_preview.set('Saved. Fetch orders to preview current priorities and your assignment.')
        return True
    def _orders_fetch(self):
        if self.guild_fetching:return
        try:
            if not self.discord_connection:raise ValueError()
            future=self.discord_connection.fetch_orders()
        except Exception:self.guild_preview.set('Guild reading needs a connected bot with channel access and Message Content Intent.');self.guild_retry_at=time.time()+60;return
        self.guild_fetching=True
        def done(result):
            try:answer=result.result();event={'kind':'guild_orders','answer':answer}
            except Exception:event={'kind':'guild_orders','error':True}
            self.events.put(event)
        future.add_done_callback(done)
    def _orders_event(self,event):
        if event.get('kind')=='guild_defense_done':
            self.guild_preview.set(event['message']);self._append(self.remote_log,event['message']);return True
        if event.get('kind')!='guild_orders':return False
        self.guild_fetching=False;self.guild_next_read=time.time()+300
        if event.get('error'):
            self.guild_plan={};self.guild_retry_at=time.time()+300;self.guild_preview.set('Could not read guild channels. Check View Channel, Read Message History and Message Content Intent.');return True
        config=self.guild_config;answer=event['answer']
        alias=config.get('alias')or(getattr(self,'current_snapshot',{}).get('user_data')or{}).get('name','')
        self.guild_plan=parse_orders(answer.get('attack',[]),answer.get('defense',[]),owner_id=self.discord_owner.get(),aliases=[alias],leader_ids=config.get('leaders','').split(',')if config.get('leaders')else())
        for key in ('attack','defense'):
            if answer.get(key+'_error'):self.guild_plan[key]={'error':'Bot cannot read this source channel. Check its channel permissions and Message Content Intent.'}
        attack=self.guild_plan['attack'];defense=self.guild_plan['defense']
        stamp=lambda source:datetime.fromtimestamp(source['at']).strftime('%a %b %d %H:%M')if source.get('at')else'no current post'
        self.guild_preview.set('Attack ['+stamp(attack)+']: '+(' → '.join(attack.get('targets',[]))or attack.get('error','Unavailable'))+'\nDefense ['+stamp(defense)+']: '+str(defense.get('island')or defense.get('error','Unavailable')))
        return True
    def _orders_ready(self):
        if not(self.guild_config.get('use_attack')or self.guild_config.get('use_defense')):return True
        if not self.guild_plan or time.time()-self.guild_plan.get('fetched_at',0)>600:
            if time.time()>=self.guild_retry_at:self._orders_fetch()
            return False
        for key,flag in (('attack','use_attack'),('defense','use_defense')):
            if self.guild_config.get(flag)and self.guild_plan.get(key,{}).get('error'):return False
        return True

    def _guild_apply_defense(self,manual=False):
        if CommandCenterMixin._browser_action_busy(self):
            if manual:self.guild_preview.set('Wait for current automation before applying defense.')
            return False
        if not self.guild_plan or self.guild_plan.get('defense',{}).get('error'):
            if manual:self.guild_preview.set('Fetch a clear, current Calculon defense assignment first.')
            return False
        self.guild_next_defense=time.time()+300;self.guild_defense_stop.clear()
        from copy import deepcopy
        plan=deepcopy(self.guild_plan)
        def work():
            try:message=apply_assigned_defense(ATClient(load_credentials(),timeout=30),self.command_schema.store,plan,should_stop=self.guild_defense_stop.is_set)
            except ValueError as error:message=str(error)
            except Exception:message='Defense assignment could not finish. Any unconfirmed change stays recorded and will not be repeated.'
            self.events.put({'kind':'guild_defense_done','message':message})
        self.guild_defense_thread=threading.Thread(target=work,daemon=True,name='Commander guild defense');self.guild_defense_thread.start();return True
    def _guild_tick(self):
        if getattr(self,'_closing',False)or getattr(self,'guild_suspended',False):return
        if self.guild_config.get('enabled')and time.time()>=max(self.guild_retry_at,self.guild_next_read):self._orders_fetch()
        if self.guild_config.get('auto_defense')and time.time()>=self.guild_next_defense:self._guild_apply_defense()
