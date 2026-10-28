import tkinter as tk
from tkinter import ttk
import unittest
from atcommander.app import ATCommanderApp
from atcommander.appearance import apply_style


class EventsLayoutTests(unittest.TestCase):
    def test_challenge_controls_share_rumble_column_and_fit_above_log(self):
        root=tk.Tk();self.addCleanup(root.destroy)
        apply_style(root)
        root.geometry('1500x700')
        app=object.__new__(ATCommanderApp);app.settings={}
        app.events_tab=ttk.Frame(root);app.events_tab.pack(fill='both',expand=True)
        app._build_events()
        app.challenge_status.set('Direct API ready • 6 active and 0 upcoming Challenge/Clash/Melee event(s).')
        root.update()
        self.assertEqual(app.challenge_card.master,app.rumble_offense_deck_combo.master)
        self.assertGreater(app.challenge_combo.winfo_rooty(),app.rumble_offense_deck_combo.winfo_rooty())
        log_top=app.event_log.winfo_rooty()
        self.assertLess(app.challenge_offense_hero_combo.winfo_rooty()+app.challenge_offense_hero_combo.winfo_height(),log_top)
        self.assertGreater(app.event_log.winfo_height(),40)
        self.assertLess(app.challenge_card.winfo_rooty()+app.challenge_card.winfo_height(),log_top)
        self.assertFalse(app.melee_character_first_var.get())


if __name__=='__main__':unittest.main()
