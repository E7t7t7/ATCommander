"""Quiet earned-reward cleanup when other account-changing work is idle."""
import threading
import time
import tkinter as tk
from tkinter import ttk
from .api import ATClient
from .credentials import load_credentials
from .command_center import CommandCenterMixin
from .settings import load_settings,save_settings
from .local_commands import CommandUnavailable
from .utility_commands import build_service
from .reward_cleanup import cleanup_earned_rewards


class RewardCleanupMixin:
    def _build_reward_controls(self,parent):
        self.reward_thread=None;self.reward_stop=threading.Event();self.reward_due=time.monotonic()+25
        self.reward_auto=tk.BooleanVar(value=self.settings.get('auto_claim_rewards',True) is True)
        self.reward_button=ttk.Button(parent,text='Claim earned rewards',command=lambda:self._reward_launch(manual=True))
        self.reward_button.pack(side='right',padx=5)
        ttk.Checkbutton(parent,text='Auto-claim rewards',variable=self.reward_auto,command=self._reward_auto_changed).pack(side='right')

    def _reward_auto_changed(self):
        stored=load_settings();stored['auto_claim_rewards']=bool(self.reward_auto.get())
        self.settings['auto_claim_rewards']=stored['auto_claim_rewards'];save_settings(stored)
        self.reward_due=time.monotonic()+2

    def _reward_launch(self,manual=False):
        if getattr(self,'_closing',False):return False
        if CommandCenterMixin._browser_action_busy(self):
            if manual:self._log('Reward cleanup waits until active automation finishes or is stopped.')
            return False
        self.reward_stop.clear();self.reward_due=time.monotonic()+900
        self.reward_button.configure(state='disabled',text='Claiming rewards…')
        def work():
            try:
                client=ATClient(load_credentials(),timeout=45,should_stop=self.reward_stop.is_set)
                service=build_service(client,store=self.command_schema.store)
                service.stop=self.reward_stop
                answer=cleanup_earned_rewards(service,limit=40)
                self.events.put({'kind':'reward_cleanup_done','answer':answer})
            except CommandUnavailable as error:
                self.events.put({'kind':'reward_cleanup_done','error':str(error)})
            except Exception:
                self.events.put({'kind':'reward_cleanup_done','error':'Reward cleanup could not finish. Unconfirmed claims remain recorded and are not repeated.'})
        self.reward_thread=threading.Thread(target=work,daemon=True,name='Commander earned rewards')
        self.reward_thread.start();return True

    def _reward_tick(self):
        if not getattr(self,'_closing',False)and self.reward_auto.get()and time.monotonic()>=self.reward_due:
            self._reward_launch()

    def _reward_event(self,event):
        if event.get('kind')!='reward_cleanup_done':return False
        if getattr(self,'_closing',False):return True
        self.reward_button.configure(state='normal',text='Claim earned rewards')
        if event.get('error'):self._log('Rewards: '+event['error']);return True
        answer=event['answer']
        if answer.get('pending'):self._log('An older reward claim remains unconfirmed and was skipped. Other earned rewards can still be checked.')
        self._log(f"Rewards: {answer['verified_advances']} reward source(s) advanced; {answer['confirmed']} claim request(s) confirmed; {answer['unavailable']} unavailable. Earned collection, quest and event rewards checked.")
        return True
