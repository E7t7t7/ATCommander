"""Synthetic Adventure terminal events; no Tk windows or account requests."""
from queue import Queue
import unittest
from unittest.mock import Mock,patch

from atcommander import app as app_module
from atcommander.app import ATCommanderApp,ADVENTURE_MODES


class Value:
    def __init__(self,value):self.value=value
    def get(self):return self.value
    def set(self,value):self.value=value


def application():
    app=object.__new__(ATCommanderApp)
    app._command_event=Mock(return_value=False);app._append=Mock();app._event_log=Mock()
    app.adventure_log=object();app.adventure_completion=Value("Last verified stage")
    app.adventure_continuous=Value(True);app.lifetime_data={};app.root=Mock()
    app.start_adventure=Mock();app._closing=False
    return app


def deliver(app,kind,**fields):
    ATCommanderApp._handle_event(app,{"kind":kind,"mode":"Adventure",**fields})


class AdventurePauseStatusTests(unittest.TestCase):
    def messages(self,app):return [call.args[1] for call in app._append.call_args_list]

    def assert_no_completion_claim(self,app):
        self.assertFalse(any("farm complete" in message.casefold() for message in self.messages(app)))

    def test_reported_pending_action_pause_survives_finished(self):
        app=application();reason="A previous Adventure start or move has an unconfirmed outcome. It will not be replayed."
        deliver(app,"adventure_paused",message=reason)
        paused=app.adventure_completion.get()
        deliver(app,"finished",paused=True,stopped=False)
        self.assertEqual(app.adventure_completion.get(),paused)
        self.assertIn(reason,self.messages(app)[-1]);self.assert_no_completion_claim(app)

    def test_legacy_finished_without_pause_flag_cannot_erase_explicit_pause(self):
        app=application();deliver(app,"adventure_paused",message="Fresh stage progress is inconsistent.")
        paused=app.adventure_completion.get()
        deliver(app,"finished",stopped=False,message="Run ended")
        self.assertEqual(app.adventure_completion.get(),paused);self.assert_no_completion_claim(app)

    def test_finished_pause_is_self_contained_when_prior_event_is_missing(self):
        for details in ({"pause_reason":"The pending battle needs review."},{"message":"The current island is unavailable."},{}):
            app=application()
            with self.subTest(details=details):deliver(app,"finished",paused=True,stopped=False,**details)
            self.assertIn("paused",app.adventure_completion.get().casefold());self.assert_no_completion_claim(app)
            for reason in details.values():self.assertIn(reason,app.adventure_completion.get())

    def test_stop_with_unconfirmed_outcome_retains_safety_reason(self):
        app=application();deliver(app,"adventure_paused",message="The charged battle outcome is not confirmed.")
        paused=app.adventure_completion.get();deliver(app,"finished",paused=True,stopped=True)
        self.assertEqual(app.adventure_completion.get(),paused);self.assert_no_completion_claim(app)

    def test_fatal_is_visible_and_later_generic_finished_cannot_claim_completion(self):
        app=application();deliver(app,"fatal",message="The pending journal cannot be verified.")
        paused=app.adventure_completion.get()
        self.assertIn("paused after error",paused)
        self.assertIn("pending journal",paused)
        deliver(app,"finished",stopped=False)
        self.assertEqual(app.adventure_completion.get(),paused);self.assert_no_completion_claim(app)

    def test_error_preserves_a_more_specific_prior_pause_reason(self):
        app=application();deliver(app,"adventure_paused",message="A previous fight for this island is still unconfirmed.")
        paused=app.adventure_completion.get();deliver(app,"fatal",message="Adventure run failed")
        deliver(app,"finished",paused=True,pause_reason="Paused")
        self.assertEqual(app.adventure_completion.get(),paused)
        self.assertIn("ERROR: Adventure run failed",self.messages(app));self.assert_no_completion_claim(app)

    def test_pause_fatal_and_finished_never_arm_or_launch_continuous_retry(self):
        for sequence in (("adventure_paused","finished"),("fatal","finished"),("finished",)):
            app=application()
            with self.subTest(sequence=sequence),patch.object(app_module,"AdventureRunner") as runner, \
                 patch.object(app_module.threading,"Thread") as thread,patch.object(app_module,"ATClient") as client:
                for kind in sequence:deliver(app,kind,message="Unconfirmed action",paused=True,stopped=False)
            runner.assert_not_called();thread.assert_not_called();client.assert_not_called()
            app.root.after.assert_not_called();app.start_adventure.assert_not_called()
            self.assertTrue(app.adventure_continuous.get(),"Keep the user's monitor preference without arming work.")
            self.assert_no_completion_claim(app)

    def test_refresh_still_updates_energy_without_hiding_unresolved_pause(self):
        app=application();app.adventure_energy_var=Value("unknown")
        deliver(app,"adventure_paused",message="Finish or check the old battle before resuming.")
        paused=app.adventure_completion.get()
        deliver(app,"adventure_progress_v2",selected="Default",energy=176,max_energy=176,prefs={})
        self.assertEqual(app.adventure_completion.get(),paused)
        self.assertIn("176",app.adventure_energy_var.get())
        self.assertTrue(any(message.startswith("Refresh:") for message in self.messages(app)))

    def test_ordinary_finished_batch_is_not_claimed_as_full_farm_completion(self):
        for stopped in (False,True):
            app=application();deliver(app,"finished",stopped=stopped,paused=False)
            self.assertEqual(app.adventure_completion.get(),"Adventure run stopped." if stopped else "Adventure run finished.")
            self.assert_no_completion_claim(app)

    def test_concrete_runner_target_clears_an_old_monitor_wait(self):
        app=application();deliver(app,"adventure_paused",message="Waiting for an island to unlock.")
        deliver(app,"adventure_target",mission_id=9261,island="Costume Party",stage=1,wins=9,target_wins=12,energy_cost=1,farming=False)
        self.assertIn("Costume Party",app.adventure_completion.get());self.assertIsNone(app._adventure_pause_message)
        deliver(app,"finished",paused=False,stopped=False)
        self.assertEqual(app.adventure_completion.get(),"Adventure run finished.")

    def test_verified_return_to_farm_supersedes_a_prior_unlock_wait(self):
        app=application();deliver(app,"adventure_paused",message="Waiting for an island to unlock.")
        deliver(app,"adventure_farm_resume",mission_id=107)
        self.assertIsNone(app._adventure_pause_message)
        self.assertIn("107",app.adventure_completion.get())
        deliver(app,"finished",stopped=True)
        self.assertEqual(app.adventure_completion.get(),"Adventure run stopped.")

    def test_explicit_new_start_resets_old_terminal_state_and_retains_preferences(self):
        app=application();app.adventure_runner=None;app.events=Queue()
        app._adventure_pause_message="Old unconfirmed action";app._adventure_error_message="Old failure"
        app._command_blocks_runner=Mock(return_value=False);app._save_settings=Mock()
        app._adventure_mission_changed=Mock()
        app.adventure_kind=Value("Custom / Event mission ID");app.adventure_mission_id=Value("107")
        app.adventure_limit=Value("0");app.adventure_reserve=Value("1")
        app.adventure_mode=Value(ADVENTURE_MODES[1]);app.adventure_completion_goal=Value("100% mastery")
        app.delay_var=Value(".1");app.battle_delay_var=Value("1.5");app.auto_ads_var=Value(False)
        with patch.object(app_module,"AdventureRunner") as runner:
            ATCommanderApp.start_adventure(app)
        self.assertIsNone(app._adventure_pause_message);self.assertIsNone(app._adventure_error_message)
        runner.return_value.start.assert_called_once();self.assertTrue(runner.call_args.args[5])
        self.assertEqual(app.adventure_mission_id.get(),"107");self.assertTrue(app.adventure_continuous.get())

    def test_other_mode_finished_does_not_change_adventure_pause(self):
        app=application();deliver(app,"adventure_paused",message="Old Adventure state needs review.")
        paused=app.adventure_completion.get()
        ATCommanderApp._handle_event(app,{"kind":"finished","mode":"Rumble","stopped":False})
        self.assertEqual(app.adventure_completion.get(),paused)
        app._event_log.assert_called_once_with("Rumble run complete.")


if __name__=="__main__":unittest.main()
