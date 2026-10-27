import copy
import unittest
from types import SimpleNamespace
from atcommander.login_bonus import CALENDARS,claim_daily_login,login_bonus_status
from atcommander.local_commands import CommandUnavailable

def calendar(flag=1,claimed=0,offered=1,cycle=10):
    return {"can_claim":flag,"num_bonuses":offered,"num_bonuses_claimed":claimed,"cycle_id":cycle}

class Service:
    def __init__(self,data,confirm=True):
        self.data=data;self.calls=[];self.confirm=confirm
        self.context=SimpleNamespace(invalidate=lambda:None)
    def snapshot(self,refresh=False):return copy.deepcopy(self.data)
    def call(self,name):
        self.calls.append(name)
        if isinstance(self.confirm,Exception):raise self.confirm
        if self.confirm:
            for key in CALENDARS:
                self.data[key]["can_claim"]=0;self.data[key]["num_bonuses_claimed"]=self.data[key]["num_bonuses"]
        return {"claimed_monthly_daily_reward":True}

class LoginBonusTests(unittest.TestCase):
    def test_missing_calendar_or_ambiguous_flags_never_claim(self):
        for data in ({},{CALENDARS[0]:calendar()},{key:calendar(flag=True) for key in CALENDARS},{key:calendar(flag=0,claimed=0,offered=1) for key in CALENDARS}):
            service=Service(data)
            with self.assertRaises(CommandUnavailable):claim_daily_login(service)
            self.assertEqual(service.calls,[])
    def test_already_claimed_does_not_send_a_request(self):
        service=Service({key:calendar(flag=0,claimed=1) for key in CALENDARS})
        self.assertTrue(claim_daily_login(service).data["already_claimed"])
        self.assertEqual(service.calls,[])
    def test_one_native_claim_verifies_both_pending_calendars(self):
        service=Service({key:calendar() for key in CALENDARS})
        answer=claim_daily_login(service)
        self.assertTrue(answer.data["claimed"]);self.assertEqual(answer.data["calendars"],2)
        self.assertEqual(service.calls,["claimDailyBonus"])
        self.assertFalse(login_bonus_status(service.data)["available"])
    def test_one_pending_calendar_does_not_require_another_new_reward(self):
        service=Service({CALENDARS[0]:calendar(),CALENDARS[1]:calendar(flag=0,claimed=1)})
        self.assertEqual(claim_daily_login(service).data["calendars"],1)
    def test_unverified_outcome_is_never_replayed(self):
        service=Service({key:calendar() for key in CALENDARS},confirm=False)
        with self.assertRaisesRegex(CommandUnavailable,"No claim was replayed"):claim_daily_login(service)
        self.assertEqual(service.calls,["claimDailyBonus"])
    def test_network_failure_is_never_replayed(self):
        service=Service({key:calendar() for key in CALENDARS},confirm=TimeoutError())
        with self.assertRaises(TimeoutError):claim_daily_login(service)
        self.assertEqual(service.calls,["claimDailyBonus"])
    def test_reset_cycle_during_claim_stops_without_another_request(self):
        service=Service({key:calendar() for key in CALENDARS})
        original=service.call
        def call(name):
            answer=original(name);service.data[CALENDARS[0]]["cycle_id"]+=1;return answer
        service.call=call
        with self.assertRaisesRegex(CommandUnavailable,"counters did not confirm"):claim_daily_login(service)
        self.assertEqual(len(service.calls),1)
    def test_stop_before_claim_spends_nothing(self):
        service=Service({key:calendar() for key in CALENDARS});service.stop=SimpleNamespace(is_set=lambda:True)
        self.assertFalse(claim_daily_login(service).data["claimed"])
        self.assertEqual(service.calls,[])
