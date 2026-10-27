import unittest
from types import SimpleNamespace
from unittest.mock import patch

from atcommander.api import ATApiError
from atcommander.arena_runner import ArenaRunner
from atcommander.local_commands import CommandInputError
from tests import test_utility_commands as fixture


class ArenaTurnLimitTests(unittest.TestCase):
    def runner(self, *, bypass=False, end_at=33, stop_at=None, missing_at=None):
        runner=ArenaRunner(bypass_turn_limit=bypass)
        runner.policy=SimpleNamespace(choose=lambda state: SimpleNamespace(
            action='combo',card_uid=1,card_uid2=2,reason='fixture',score=1))
        calls=[]
        def play(**fields):
            calls.append(fields)
            if stop_at==len(calls):runner.stop()
            if missing_at==len(calls):return {'result':False}
            return {'battle_data':{'battle_id':'fixture','host_id':'host',
                    'finished':len(calls)>=end_at},'won':True}
        runner._client=SimpleNamespace(play_battlefield_card=play,
            extract_battle_data=lambda answer:answer.get('battle_data'),
            battle_finished=lambda answer,state:bool(state and state.get('finished')),
            extract_outcome=lambda answer,state:answer.get('won'))
        return runner,calls

    def drive(self,runner):
        with patch('atcommander.arena_runner.time.sleep'):
            return runner._drive_one({'battle_id':'fixture','host_id':'host'},1,'fixture')

    def test_default_stops_after_thirty_without_an_extra_play(self):
        runner,calls=self.runner()
        with self.assertRaisesRegex(ATApiError,'exceeded 30'):self.drive(runner)
        self.assertEqual(len(calls),30)

    def test_bypass_reaches_real_result_after_thirty_and_next_battle_runs(self):
        runner,calls=self.runner(bypass=True)
        self.assertEqual(self.drive(runner)[:2],(True,33))
        self.assertEqual(len(calls),33)
        self.assertEqual(self.drive(runner)[:2],(True,1))
        self.assertEqual(len(calls),34)

    def test_default_accepts_result_on_thirtieth_play(self):
        runner,calls=self.runner(end_at=30)
        self.assertEqual(self.drive(runner)[:2],(True,30))
        self.assertEqual(len(calls),30)

    def test_stop_still_interrupts_bypass_after_thirtieth_play(self):
        runner,calls=self.runner(bypass=True,end_at=999,stop_at=32)
        self.assertEqual(self.drive(runner)[:2],(None,32))
        self.assertEqual(len(calls),32)

    def test_unconfirmed_response_is_never_replayed_in_bypass(self):
        runner,calls=self.runner(bypass=True,missing_at=31)
        with self.assertRaisesRegex(ATApiError,'not retried'):self.drive(runner)
        self.assertEqual(len(calls),31)


class ArenaTurnCommandTests(unittest.TestCase):
    setUp=fixture.UtilityTests.setUp

    def test_other_mode_rejects_bypass_before_any_game_call(self):
        for mode in ('adventure','melee','swole','rumble','pvp-challenge'):
            with self.subTest(mode=mode):
                self.client.calls.clear()
                with self.assertRaises(CommandInputError):
                    self.service.execute('play',{'mode':mode,'bypass_arena_turn_limit':True})
                self.assertEqual(self.client.calls,[])

    def test_play_schema_defaults_off_and_exposes_option(self):
        spec=next(spec for spec in self.service.specs() if spec.name=='play')
        option=next(arg for arg in spec.args if arg.name=='bypass_arena_turn_limit')
        self.assertIs(option.default,False)


if __name__=='__main__':unittest.main()
