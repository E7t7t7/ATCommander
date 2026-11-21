import tempfile
import tkinter as tk
from pathlib import Path
import unittest
from unittest.mock import patch
from atcommander.app import ATCommanderApp
from atcommander.command_store import CommandStore

class RemoteScrollTests(unittest.TestCase):
    def setUp(self):
        self.root=tk.Tk();self.tmp=tempfile.TemporaryDirectory();self.store=CommandStore(Path(self.tmp.name)/'state.json')
        with patch.object(ATCommanderApp,'_refresh_account_badge'),patch.object(ATCommanderApp,'_refresh_account_metadata'),patch('atcommander.utility_commands.CommandStore',return_value=self.store),patch('atcommander.app.migrate_last_siege_browser_replay_metadata',return_value=False),patch('atcommander.app.migrate_last_native_replay_metadata',return_value=[]):self.app=ATCommanderApp(self.root)
        self.root.geometry('1340x760');self.app.nb.select(self.app.remote_tab);self.root.update_idletasks()
    def tearDown(self):
        for timer in self.root.tk.call('after','info'):
            try:self.root.after_cancel(timer)
            except tk.TclError:pass
        self.root.destroy();self.tmp.cleanup()
    def test_wide_scrollbars_and_navigation_are_visible_inside_window(self):
        panes=self.app.remote_panes;bar=panes.controls_scrollbar;horizontal=panes.controls_horizontal
        self.assertTrue(bar.winfo_ismapped());self.assertGreaterEqual(bar.winfo_width(),22)
        self.assertLessEqual(bar.winfo_rootx()+bar.winfo_width(),self.root.winfo_rootx()+self.root.winfo_width())
        self.assertTrue(horizontal.winfo_ismapped());self.assertGreater(horizontal.winfo_height(),10)
        self.assertLess(panes.controls_canvas.yview()[1],1)
        for button in self.app.remote_jump_buttons.values():self.assertTrue(button.winfo_ismapped())
    def test_default_shortcut_reaches_siege_controls_without_touching_defaults(self):
        before=self.store.read()['scheduled_event_defaults']
        self.app.remote_jump_buttons['event_defaults_box'].invoke();self.root.update_idletasks()
        canvas=self.app.remote_panes.controls_canvas;widget=self.app.default_widgets['Siege']['island']
        self.assertGreater(canvas.yview()[0],0)
        self.assertGreaterEqual(widget.winfo_rooty(),canvas.winfo_rooty())
        self.assertLess(widget.winfo_rooty()+widget.winfo_height(),canvas.winfo_rooty()+canvas.winfo_height())
        self.assertEqual(self.store.read()['scheduled_event_defaults'],before)
    def test_guild_shortcut_and_top_shortcut_scroll_in_both_directions(self):
        canvas=self.app.remote_panes.controls_canvas
        self.app.remote_jump_buttons['guild_orders_box'].invoke();self.root.update_idletasks();self.assertGreater(canvas.yview()[0],0)
        self.app.remote_jump_buttons['remote_connection_box'].invoke();self.root.update_idletasks();self.assertEqual(canvas.yview()[0],0)
    def test_mouse_wheel_over_form_label_scrolls_but_entries_keep_their_own_wheel(self):
        canvas=self.app.remote_panes.controls_canvas;head=self.app.remote_connection_box
        label=next(w for w in head.winfo_children()if w.winfo_class()=='TLabel')
        self.assertTrue(any(tag.startswith('CommanderControls')for tag in label.bindtags()))
        label.event_generate('<MouseWheel>',delta=-120);self.root.update_idletasks();self.assertGreater(canvas.yview()[0],0)
        entry=self.app.default_widgets['Siege']['island']
        self.assertFalse(any(tag.startswith('CommanderControls')for tag in entry.bindtags()))
    def test_log_remains_separate_when_controls_scroll_and_window_shrinks(self):
        self.root.geometry('1340x700');self.root.update_idletasks();self.app.remote_jump_buttons['guild_orders_box'].invoke();self.root.update_idletasks()
        log=self.app.remote_log;self.assertGreater(log.winfo_height(),30)
        self.assertGreater(log.winfo_rooty(),self.app.remote_panes.controls_canvas.winfo_rooty()+self.app.remote_panes.controls_canvas.winfo_height())
