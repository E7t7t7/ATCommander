import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from test_research_automation import FakeService
from atcommander.command_center import SchemaClient
from atcommander.command_store import CommandStore
from atcommander.research_automation import ResearchAutomation
from atcommander.utility_commands import build_service


class ResearchAdIntegrationTests(unittest.TestCase):
    def test_unsupported_unlock_is_skipped_before_mutation_ledger(self):
        callback=Mock();callback.supported_phases={'speedup'}
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'queue.json'
            engine=ResearchAutomation(FakeService(),path,ad_callback=callback)
            self.assertFalse(engine._research_ad(3,'unlock',{'pair':'22~23','card1':22,'card2':23}))
            callback.assert_not_called()
            self.assertFalse(path.exists())

    def test_command_uses_automatic_playback_without_legacy_calibration(self):
        with tempfile.TemporaryDirectory() as directory:
            service=build_service(SchemaClient(),store=CommandStore(Path(directory)/'preferences.json'))
            with patch('atcommander.research_ad_ui.play_research_ad',return_value={'ok':True,'verified':True}) as playback, \
                 patch('atcommander.ad_ui.attempt_legitimate_rewarded_flow') as legacy:
                service.execute('research-ad')
            playback.assert_called_once()
            legacy.assert_not_called()
            self.assertEqual(service.research_ad_callback.supported_phases,{'speedup'})

    def test_queue_passes_frozen_target_and_stop_to_automatic_playback(self):
        with tempfile.TemporaryDirectory() as directory:
            service=build_service(SchemaClient(),store=CommandStore(Path(directory)/'preferences.json'))
            context={'phase':'speedup','slot_index':3,'pair':'22~23','task_index':9,'before_end':4000}
            with patch('atcommander.research_ad_ui.play_research_ad',return_value={'ok':False}) as playback:
                service.research_ad_callback(service,{'_commander_research':context})
            self.assertEqual(playback.call_args.kwargs['context'],context)
            self.assertTrue(callable(playback.call_args.kwargs['should_stop']))
