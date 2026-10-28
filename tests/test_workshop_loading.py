from copy import deepcopy
import queue
import time
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import Mock,patch
from atcommander.app import ATCommanderApp
from test_deck_builder import fixture


class WorkshopLoadingTests(unittest.TestCase):
    def test_open_loads_characters_traits_and_selected_deck_without_manual_refresh(self):
        root=tk.Tk();self.addCleanup(root.destroy);root.geometry('1300x650')
        app=object.__new__(ATCommanderApp);app._closing=False;app.events=queue.Queue();app.deck_rows=[]
        app.deck_builder_tab=ttk.Frame(root);app.deck_builder_tab.pack(fill='both',expand=True)
        app._build_deck_builder()
        live,catalog,recipes=fixture()
        live['user_data']={'active_deck':24}
        live['user_decks']={'24':{'deck_id':24,'name':'My deck','commander':{'unit_id':1001},'units':[{'unit_index':i} for i in range(1,26)]}}
        client=Mock();client.init.return_value=deepcopy(live);client.get_exact_decks.return_value={}
        client.card_catalog.return_value=catalog;client.combo_catalog.return_value=recipes
        with patch('atcommander.deck_builder_ui.ATClient',return_value=client),patch('atcommander.deck_builder_ui.load_credentials'):
            app._builder_opened()
            app.deck_builder_thread.join(3)
            self.assertFalse(app.deck_builder_thread.is_alive())
            app._deck_builder_event(app.events.get(timeout=1))
        root.update()
        self.assertIn('Amy',app.builder_character_combo['values'])
        self.assertIn('athletic',app.builder_trait_combo['values'])
        self.assertEqual(app.builder_name.get(),'My deck')
        self.assertEqual(len(app.builder_draft_table.get_children()),25)
        self.assertGreater(app.builder_draft_table.winfo_height(),60)
        self.assertLess(app.builder_draft_table.winfo_rooty(),root.winfo_rooty()+root.winfo_height())
        self.assertEqual(client.call.call_count,0)


if __name__=='__main__':unittest.main()
