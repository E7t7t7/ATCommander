from queue import Queue
import threading
from types import SimpleNamespace
import tkinter as tk
from tkinter import ttk
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
from atcommander.client_update_ui import ClientUpdateMixin
from atcommander.appearance import apply_style,friendly_label,soften_labels
from atcommander.command_store import CommandStore

class Value:
    def __init__(self,value=""):self.value=value
    def get(self):return self.value
    def set(self,value):self.value=value
class Control:
    def configure(self,**kwargs):self.settings=kwargs
class ClientUi(ClientUpdateMixin):
    def __init__(self):
        self._closing=False;self.client_update_thread=None;self.client_update_stop=threading.Event()
        self.client_update_status=Value();self.client_update_button=Control();self.events=Queue()

class ClientUpdateUiTests(unittest.TestCase):
    def test_worker_uses_public_updater_and_defers_widget_changes_to_events(self):
        app=ClientUi()
        def immediate(*,target,**kwargs):return SimpleNamespace(start=target,is_alive=lambda:False)
        with patch("atcommander.client_update_ui.threading.Thread",side_effect=immediate), \
             patch("atcommander.client_update_ui.update_public_client_profile",return_value={"message":"Client checked."}) as update:
            app._start_client_update()
        update.assert_called_once()
        self.assertEqual(app.client_update_status.get(),"Checking the official game client…")
        event=app.events.get_nowait();self.assertEqual(event["kind"],"client_update_done")
        app._client_update_event(event);self.assertEqual(app.client_update_status.get(),"Client checked.")
        self.assertEqual(app.client_update_button.settings["text"],"Check / update game client")

    def test_pressing_active_check_requests_cancellation_without_a_second_worker(self):
        app=ClientUi();app.client_update_thread=SimpleNamespace(is_alive=lambda:True)
        with patch("atcommander.client_update_ui.update_public_client_profile",side_effect=AssertionError("No repeat")):
            app._start_client_update()
        self.assertTrue(app.client_update_stop.is_set())

    def test_closing_prevents_a_check_and_ignores_result_widget_updates(self):
        app=ClientUi();app._closing=True
        with patch("atcommander.client_update_ui.threading.Thread",side_effect=AssertionError("No launch")):
            app._start_client_update()
        self.assertTrue(app._client_update_event({"kind":"client_update_done","message":"late"}))
        self.assertEqual(app.client_update_status.get(),"")

    def test_presentation_labels_leave_state_values_and_acronyms_distinct(self):
        self.assertEqual(friendly_label("RESEARCH EVERY OWNED COMBO"),"Research your combos")
        self.assertEqual(friendly_label("REVIEW LIVE CM DATA"),"Review live CM data")
        self.assertEqual(friendly_label("Item → Combo (default)"),"Item → Combo (default)")

    def test_real_widgets_keep_their_commands_and_values_after_the_dark_cleanup(self):
        try:root=tk.Tk()
        except tk.TclError:self.skipTest("Tk display unavailable")
        root.withdraw();self.addCleanup(root.destroy);apply_style(root)
        called=[];button=ttk.Button(root,text="START ADVENTURE",command=lambda:called.append(True))
        entry_value=tk.StringVar(root,value="COMPLETE")
        ttk.Entry(root,textvariable=entry_value).pack();button.pack()
        soften_labels(root)
        self.assertEqual(button.cget("text"),"Start Adventure")
        self.assertEqual(entry_value.get(),"COMPLETE")
        button.invoke();self.assertEqual(called,[True])
        self.assertEqual(ttk.Style(root).lookup("TFrame","background"),"#10151d")

    def test_full_app_keeps_all_tabs_and_updates_active_navigation_without_network(self):
        from atcommander import app as module
        from atcommander.settings import DEFAULTS
        try:root=tk.Tk()
        except tk.TclError:self.skipTest("Tk display unavailable")
        root.withdraw();self.addCleanup(root.destroy)
        with tempfile.TemporaryDirectory() as directory:
            store=CommandStore(Path(directory)/"preferences.json")
            with patch.object(module,"load_settings",return_value=dict(DEFAULTS)), \
                 patch.object(module,"LifetimeTracker",return_value=SimpleNamespace(to_dict=lambda:{})), \
                 patch.object(module,"LearningBrowser"), \
                 patch.object(module,"migrate_last_siege_browser_replay_metadata",return_value=False), \
                 patch.object(module,"migrate_last_native_replay_metadata",return_value=[]), \
                 patch.object(module,"load_profiles",return_value={}), \
                 patch.object(module.ATCommanderApp,"_refresh_account_badge"), \
                 patch.object(module.ATCommanderApp,"_refresh_account_metadata"), \
                 patch.object(module.ATCommanderApp,"_build_game_panel"), \
                 patch("atcommander.utility_commands.CommandStore",return_value=store):
                app=module.ATCommanderApp(root)
            def cancel_callbacks():
                for identity in root.tk.call('after','info'):
                    try:root.after_cancel(identity)
                    except tk.TclError:pass
            self.addCleanup(cancel_callbacks)
            self.assertEqual(len(app.nb.tabs()),12)
            self.assertEqual(len(app.nav_buttons),12)
            self.assertEqual(app.client_update_button.cget("text"),"Check / update game client")
            self.assertEqual(app.app_update_button.cget("text"),"Update Commander")
            self.assertEqual(app.builder_rule.get(),"Balanced recommendation")
            self.assertIsNone(app.deck_builder_cache)
            app.nb.select(app.research_tab);app._sync_navigation()
            active=[tab for tab,button in app.nav_buttons if button.cget("style")=="Nav.Active.TButton"]
            self.assertEqual(active,[app.research_tab])
            self.assertFalse(app.research_all_speedups.get())
            self.assertFalse(app.melee_character_first_var.get())

if __name__=="__main__":unittest.main()
