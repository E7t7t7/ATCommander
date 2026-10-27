import unittest
from unittest.mock import Mock, patch
from atcommander.event_runner import EventBattleRunner

def status(phase,attacks=10,rejected=False):
    result={"guild_siege_status":{"phase":phase,"num_attacks":attacks,
        "enemy_locations":[{"slot_id":"1","hp":100,"data":{"name":"Fixture Island"}}]}}
    if rejected:result["result"]=False
    return result

class SiegeGateTests(unittest.TestCase):
    def run_scenario(self,statuses):
        events=[]
        client=Mock()
        client.init.return_value={}
        client.get_guild_siege_status.side_effect=statuses
        with patch("atcommander.event_runner.LifetimeTracker") as tracker, \
             patch("atcommander.event_runner.ATClient",return_value=client), \
             patch("atcommander.event_runner.load_credentials"), \
             patch("atcommander.event_runner.ad_preflight"), \
             patch("atcommander.event_runner.load_profiles",return_value={"siege":{"message":"fixtureStart"}}), \
             patch("atcommander.ad_ui.prepare_game_ui",return_value={"ok":True}), \
             patch("atcommander.event_runner.time.sleep"):
            tracker.return_value.to_dict.return_value={}
            runner=EventBattleRunner("Siege",event=events.append)
            runner._stop=Mock()
            runner._stop.is_set.return_value=False
            runner._pause=Mock()
            runner._pause.is_set.return_value=False
            runner._try_siege_start=Mock(return_value=({}, {"battle_id":"fixture-battle"}))
            runner._drive=Mock(return_value=(True,1))
            runner._run()
        return runner,events

    def test_finished_phase_with_stale_attacks_never_starts(self):
        runner,events=self.run_scenario([status(3)])
        runner._try_siege_start.assert_not_called()
        self.assertTrue(any("has finished" in event.get("message","") for event in events))
        self.assertEqual("finished",events[-1]["kind"])

    def test_only_proven_attack_phase_starts_after_setup(self):
        runner,_events=self.run_scenario([status(1),status(2,1),status(3)])
        self.assertEqual(1,runner._try_siege_start.call_count)
        runner._stop.wait.assert_called_once_with(60)

    def test_unknown_and_closed_phases_wait_without_attacks(self):
        runner,_events=self.run_scenario([status(0),status(99),status(3)])
        runner._try_siege_start.assert_not_called()
        self.assertEqual(2,runner._stop.wait.call_count)

    def test_rejected_status_cannot_use_nested_stale_attack_data(self):
        runner,events=self.run_scenario([status(2,rejected=True),status(3)])
        runner._try_siege_start.assert_not_called()
        self.assertTrue(any("status refresh was rejected" in event.get("message","") for event in events))

if __name__=="__main__":unittest.main()
