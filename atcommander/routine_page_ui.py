"""A simple daily-routine page backed by the existing routine engine."""
from dataclasses import asdict
import tkinter as tk
from tkinter import ttk,messagebox
from .automation_profiles import AutomationProfiles,BUILTIN_DAILY,DailyOptions
from .log_view import split_controls_log


def update_summary(app,*_):
    values=app.daily_vars
    app.routine_budget_summary.set('Spending per run: '+values['nixon_budget'].get()+' Nixons · '+values['cm_stones_budget'].get()+' CM stones. Research: '+values['research_pairs'].get()+' pairs · '+values['research_speedup_hours'].get()+' speedup hours.')


def toggle_details(app):
    if app.routine_details.get():
        app.daily_extra.grid(row=3,column=0,columnspan=6,sticky='ew',pady=(8,0));app.routine_editor.pack(fill='x',pady=8)
    else:app.daily_extra.grid_remove();app.routine_editor.pack_forget()


def run_selected(app):
    repeat=app.routine_run_mode.get()=='Repeat'
    if repeat:
        try:
            minutes=int(app.routine_repeat_minutes.get())
            if not 1<=minutes<=1440:raise ValueError()
        except ValueError:return messagebox.showerror('Repeat interval','Choose a repeat interval from 1 to 1440 minutes.')
        app.routine_interval.set(str(minutes*60))
    app._routine_start(repeat)


def preview_selected(app):
    if app.command_busy:return
    if any(row['command']=='daily-run'for row in app.routine_steps):
        app.routine_state.set('Preparing preview…');app._routine_preview_daily()
    else:
        app.routine_details.set(True);toggle_details(app)
        app.routine_state.set('Review the saved steps below. Run starts them in order.')


def save_limits(app):
    try:
        app._routine_sync_daily()
        if app.routine_profile.get()!=BUILTIN_DAILY:app.automation_profiles.save(app.routine_profile.get(),app.routine_steps)
        app.routine_state.set('Limits saved. Ready to run.')
    except ValueError as error:messagebox.showerror('Daily limits',str(error))


def build_routines(app):
    app.automation_profiles=AutomationProfiles(app.command_schema.store,app.command_schema)
    saved=app.command_schema.store.read()
    if app.routine_steps and len(app.automation_profiles.names())==1:
        try:app.automation_profiles.save('Previous routine',app.routine_steps)
        except ValueError:pass
    app.routine_panes,controls,activity=split_controls_log(app.routines_tab,controls_height=400)
    head=ttk.Frame(controls,style='Card.TFrame',padding=12);head.pack(fill='x',pady=(0,8))
    ttk.Label(head,text='Daily routines',style='Big.Card.TLabel').pack(anchor='w')
    ttk.Label(head,text='Choose a profile, check its limits, then Run. Best Daily plans from your live quests and account.',style='Muted.Card.TLabel',wraplength=1040).pack(anchor='w',pady=(4,10))
    profilebar=ttk.Frame(head,style='Card.TFrame');profilebar.pack(fill='x')
    ttk.Label(profilebar,text='Profile',style='Card.TLabel').pack(side='left',padx=(0,8))
    selected=saved.get('automation_selected_profile',BUILTIN_DAILY)
    if selected not in app.automation_profiles.names():selected=BUILTIN_DAILY
    app.routine_profile=tk.StringVar(value=selected)
    app.routine_profile_combo=ttk.Combobox(profilebar,textvariable=app.routine_profile,values=app.automation_profiles.names(),state='readonly',width=30)
    app.routine_profile_combo.pack(side='left');app.routine_profile_combo.bind('<<ComboboxSelected>>',lambda _:app._routine_load_profile())
    ttk.Button(profilebar,text='Save as…',command=app._routine_save_profile).pack(side='left',padx=8)
    app.routine_state=tk.StringVar(value='Ready · run once')
    actions=ttk.Frame(head,style='Card.TFrame');actions.pack(fill='x',pady=(12,7))
    app.routine_run_button=ttk.Button(actions,text='Run routine',style='Accent.TButton',command=lambda:run_selected(app));app.routine_run_button.pack(side='left')
    ttk.Button(actions,text='Stop',style='Danger.TButton',command=app._routine_stop).pack(side='left',padx=8)
    ttk.Button(actions,text='Preview',command=lambda:preview_selected(app)).pack(side='left')
    ttk.Label(actions,textvariable=app.routine_state,style='Card.TLabel',wraplength=620).pack(side='right')
    repeatbar=ttk.Frame(head,style='Card.TFrame');repeatbar.pack(fill='x')
    app.routine_run_mode=tk.StringVar(value='Run once');app.routine_repeat_minutes=tk.StringVar(value='5');app.routine_interval=tk.StringVar(value='300')
    repeatmode=ttk.Combobox(repeatbar,textvariable=app.routine_run_mode,values=('Run once','Repeat'),state='readonly',width=12);repeatmode.pack(side='left')
    ttk.Label(repeatbar,text='Every',style='Card.TLabel').pack(side='left',padx=(10,5))
    app.routine_repeat_control=ttk.Spinbox(repeatbar,textvariable=app.routine_repeat_minutes,from_=1,to=1440,width=6,state='disabled');app.routine_repeat_control.pack(side='left')
    ttk.Label(repeatbar,text='minutes · repeating stops when Commander closes',style='Muted.Card.TLabel').pack(side='left',padx=5)
    repeatmode.bind('<<ComboboxSelected>>',lambda _:app.routine_repeat_control.configure(state='normal'if app.routine_run_mode.get()=='Repeat'else'disabled'))
    app.daily_frame=ttk.LabelFrame(controls,text='Daily limits',padding=10);app.daily_frame.pack(fill='x',pady=(0,8))
    try:daily=asdict(DailyOptions.parse(saved.get('daily_profile_options',{})))
    except ValueError:daily=asdict(DailyOptions())
    app.daily_vars={key:tk.BooleanVar(value=value)if isinstance(value,bool)else tk.StringVar(value=str(value))for key,value in daily.items()}
    for column,(key,label)in enumerate((('max_battles','Battle limit'),('arena_reserve','Keep Arena energy'),('adventure_reserve','Keep Adventure energy'))):
        ttk.Label(app.daily_frame,text=label).grid(row=0,column=column*2,sticky='w',padx=(0,7),pady=4)
        ttk.Spinbox(app.daily_frame,textvariable=app.daily_vars[key],from_=0,to=1000,width=7).grid(row=0,column=column*2+1,sticky='w',padx=(0,18))
    options=ttk.Frame(app.daily_frame);options.grid(row=1,column=0,columnspan=6,sticky='ew',pady=5)
    ttk.Checkbutton(options,text='Include available events',variable=app.daily_vars['include_events']).pack(side='left')
    ttk.Checkbutton(options,text='Watch ads for daily benefits',variable=app.daily_vars['watch_ads']).pack(side='left',padx=12)
    ttk.Button(options,text='Save limits',command=lambda:save_limits(app)).pack(side='right')
    app.routine_budget_summary=tk.StringVar();ttk.Label(app.daily_frame,textvariable=app.routine_budget_summary,style='Muted.TLabel',wraplength=1000).grid(row=2,column=0,columnspan=6,sticky='w',pady=(4,0))
    app.daily_extra=ttk.Frame(app.daily_frame)
    for index,(key,label)in enumerate((('nixon_budget','Nixons per run'),('cm_stones_budget','CM stones per run'),('event_battles','Battles per event'),('swole_reserve','Keep Swole tickets'),('research_pairs','Research pairs'),('research_speedup_hours','Research speedup hours'))):
        row,column=divmod(index,3);column*=2
        ttk.Label(app.daily_extra,text=label).grid(row=row,column=column,sticky='w',padx=(0,8),pady=4)
        ttk.Entry(app.daily_extra,textvariable=app.daily_vars[key],width=9).grid(row=row,column=column+1,sticky='w',padx=(0,20))
    for key in ('nixon_budget','cm_stones_budget','research_pairs','research_speedup_hours'):app.daily_vars[key].trace_add('write',lambda *_:update_summary(app))
    update_summary(app)
    app.routine_daily_anchor=ttk.Frame(controls);app.routine_daily_anchor.pack(fill='x')
    app.routine_details=tk.BooleanVar(value=False)
    ttk.Checkbutton(app.routine_daily_anchor,text='More settings: spending, research & custom steps',variable=app.routine_details,command=lambda:toggle_details(app)).pack(anchor='w')
    app.routine_editor=ttk.LabelFrame(controls,text='Custom routine steps',padding=9)
    ttk.Label(app.routine_editor,text='Add commands from Command Center to build your own profile. Best Daily keeps its live quest plan.',wraplength=1000).pack(anchor='w',pady=(0,5))
    app.routine_list=tk.Listbox(app.routine_editor,bg='#151d27',fg='#dce7f4',selectbackground='#31584f',relief='flat',height=5,font=('Segoe UI',10));app.routine_list.pack(fill='x')
    buttons=ttk.Frame(app.routine_editor);buttons.pack(fill='x',pady=(8,0))
    for text,command in [('Remove step',app._routine_remove),('Clear steps',app._routine_clear),('Read-only daily check',app._routine_daily),('Delete profile',app._routine_delete_profile)]:ttk.Button(buttons,text=text,command=command).pack(side='left',padx=(0,7))
    ttk.Button(buttons,text='Open Command Center',command=lambda:app.nb.select(app.commands_tab)).pack(side='right')
    app.routine_log=app._logbox(activity,height=8,pady=(0,0))
    app._routine_load_profile()
