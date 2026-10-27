from queue import Queue
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from atcommander.deck_builder_ui import DeckBuilderMixin


class UI(DeckBuilderMixin):
    def __init__(self):
        self._closing=False; self.deck_builder_thread=None; self.deck_builder_stop=threading.Event()
        self.deck_builder_cache=None; self.deck_builder_draft=[]; self.events=Queue()
        self.deck_builder_status=Mock(); self.builder_name=SimpleNamespace(get=lambda:'')
    def _builder_target(self): return None


class DeckBuilderUiTests(unittest.TestCase):
    def test_catalog_failure_still_loads_saved_slots_for_renaming(self):
        app=UI(); client=Mock()
        client.init.return_value={'user_units':{},'user_decks':{'24':{'deck_id':24,'name':'Existing'}}}
        client.get_exact_decks.return_value={}
        client.card_catalog.side_effect=ConnectionError('synthetic failure')
        client.combo_catalog.side_effect=ConnectionError('synthetic failure')
        def immediate(*,target,**kwargs):return SimpleNamespace(start=target,is_alive=lambda:False)
        with patch('atcommander.deck_builder_ui.CommandCenterMixin._browser_action_busy',return_value=False), \
             patch('atcommander.deck_builder_ui.ATClient',return_value=client), \
             patch('atcommander.deck_builder_ui.load_credentials'), \
             patch('atcommander.deck_builder_ui.threading.Thread',side_effect=immediate):
            self.assertTrue(app._builder_launch('load'))
        event=app.events.get_nowait()
        self.assertNotIn('error',event)
        self.assertIn('24',event['answer']['snapshot']['user_decks'])
        self.assertEqual(event['answer']['catalog'],{})
        self.assertNotIn('synthetic failure',event['answer']['warning'])

    def test_uncertain_write_requires_fresh_load_before_another_write(self):
        app=UI(); app.deck_builder_cache={'previous':'data'}
        self.assertTrue(app._deck_builder_event({'kind':'deck_builder_done','action':'rename','error':'Not confirmed.'}))
        self.assertIsNone(app.deck_builder_cache)

    def test_active_automation_prevents_a_builder_worker_from_starting(self):
        app=UI()
        with patch('atcommander.deck_builder_ui.CommandCenterMixin._browser_action_busy',return_value=True), \
             patch('atcommander.deck_builder_ui.threading.Thread',side_effect=AssertionError('No worker')):
            self.assertFalse(app._builder_launch('load'))


if __name__=='__main__':unittest.main()
