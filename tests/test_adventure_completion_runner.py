"""Sequential island progression, exact costs, Stop and uncertain-start safety."""
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from atcommander.api import ATClient, ATApiError
from atcommander.adventure_completion import AdventureScanError
from atcommander.adventure_run_state import AdventureRunState
from atcommander.adventure_runner import AdventureRunner
from atcommander.preflight import ad_preflight


class Client:
    credentials = SimpleNamespace(user_id='synthetic-adventure-owner')
    def __init__(self, *, energy=50, wins=0):
        self.data={'result':True,'time':1000,'user_data':{'energy':energy,'max_energy':30},
                   'current_campaigns':{'1':{'completed':0}},
                   'current_missions':{'101':{'energy':2,'level':min(wins+1,13)}},
                   'mission_completions':({'101':{'mission_id':101,'campaign_id':1,'complete':wins}} if wins else {})}
        self.campaigns={'1':{'id':1,'name':'First','missions':[101,102]},
                        '2':{'id':2,'name':'Second','campaign_req':'1','missions':[103]}}
        self.missions={str(mid):{'id':mid} for mid in (101,102,103)}
        self.starts=[];self.reply=None;self.error=None;self.current_mid=None;self.stop_on_read=None
    def init(self):
        if self.stop_on_read:self.stop_on_read()
        return copy.deepcopy(self.data)
    def campaign_catalog(self):return copy.deepcopy(self.campaigns)
    def mission_catalog(self):return copy.deepcopy(self.missions)
    def call(self, message, fields):
        if message!='startMission':raise AssertionError('Unexpected mutation')
        mid=int(fields['mission_id']);self.starts.append(mid);self.current_mid=mid
        if self.error:raise self.error
        if self.reply is not None:return copy.deepcopy(self.reply)
        self.data['user_data']['energy']-=self.data['current_missions'][str(mid)]['energy']
        return {'result':True,'battle_data':{'battle_id':'synthetic-battle','host_id':'synthetic-host'}}
    def finish(self, won=True, *, credit=True, stop=None):
        mid=str(self.current_mid)
        if won and credit:
            old=self.data['mission_completions'].get(mid,{}).get('complete',0)
            cid=1 if self.current_mid in (101,102) else 2
            self.data['mission_completions'][mid]={'mission_id':int(mid),'campaign_id':cid,'complete':old+1}
            self.data['current_missions'][mid]['level']=min(old+2,13)
            if mid=='101':self.data['current_missions'].setdefault('102',{'energy':3,'level':1})
            if mid=='102':
                self.data['current_campaigns']['2']={'completed':0}
                self.data['current_missions'].setdefault('103',{'energy':4,'level':1})
        if stop:stop()
        return won,2
    opponent_name=staticmethod(lambda _state:'Fixture')


class AdventureCompletionRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'pending.json';self.events=[]
    def runner(self,client,**kwargs):
        runner=AdventureRunner(101,complete_islands=True,state_path=self.path,event=self.events.append,
                               battle_limit=kwargs.pop('battle_limit',1),**kwargs)
        runner._client=client;runner._stop.wait=Mock(return_value=False)
        runner._drive_completion=Mock(side_effect=lambda *_:client.finish())
        return runner
    def run_it(self,runner,client):
        with patch('atcommander.adventure_runner.ad_preflight'):
            runner._run_completion(client.init())
    def mastered(self,client):
        client.data['current_campaigns']['2']={'completed':1}
        for mid in (101,102,103):
            cid=1 if mid in (101,102) else 2
            client.data['current_missions'][str(mid)]={'energy':2,'level':13}
            client.data['mission_completions'][str(mid)]={'mission_id':mid,'campaign_id':cid,'complete':12}

    def test_mastery_finishes_stage_then_next_stage_then_next_island(self):
        client=Client(energy=200,wins=11);runner=self.runner(client,battle_limit=14)
        self.run_it(runner,client)
        self.assertEqual(client.starts,[101]+[102]*12+[103])
        self.assertEqual(runner.mission_id,101)
        self.assertFalse(any(event['kind']=='adventure_farm_resume' for event in self.events))

    def test_default_farm_resumes_only_after_last_stage_is_verified_mastered(self):
        client=Client(energy=50);self.mastered(client)
        client.data['mission_completions']['103']['complete']=11
        client.data['current_missions']['103']['level']=12
        runner=self.runner(client,battle_limit=2)
        self.run_it(runner,client)
        self.assertEqual(client.starts,[103,101])
        self.assertEqual(len([event for event in self.events if event['kind']=='adventure_farm_resume']),1)

    def test_locked_stage_cannot_trigger_default_farm(self):
        client=Client(wins=12);runner=self.runner(client)
        self.run_it(runner,client)
        self.assertFalse(client.starts)
        self.assertTrue(any(event['kind']=='adventure_paused' for event in self.events))

    def test_exact_cost_and_reserve_stop_before_insufficient_energy(self):
        for energy,expected in ((5,0),(6,1)):
            with self.subTest(energy=energy):
                client=Client(energy=energy);runner=self.runner(client,reserve_energy=4)
                self.run_it(runner,client)
                self.assertEqual(len(client.starts),expected)

    def test_unknown_boolean_fractional_or_conflicting_energy_sends_no_start(self):
        for changed in ({}, {'energy':True}, {'energy':5.5}, {'energy':5,'adventure_energy':6}):
            with self.subTest(changed=changed):
                client=Client();client.data['user_data']=changed;runner=self.runner(client)
                with self.assertRaises(AdventureScanError):self.run_it(runner,client)
                self.assertFalse(client.starts)

    def test_cost_increases_are_rechecked_before_next_battle(self):
        client=Client(energy=7);runner=self.runner(client,battle_limit=5,reserve_energy=2)
        def finish(*_):
            result=client.finish();client.data['current_missions']['101']['energy']=6;return result
        runner._drive_completion.side_effect=finish
        self.run_it(runner,client)
        self.assertEqual(client.starts,[101])

    def test_explicit_test_energy_budget_is_shared_across_route(self):
        client=Client(energy=100);runner=self.runner(client,battle_limit=10,energy_budget=5)
        self.run_it(runner,client)
        self.assertEqual(client.starts,[101,101])

    def test_stop_during_fresh_read_sends_no_start(self):
        client=Client();runner=self.runner(client)
        client.stop_on_read=runner.stop
        self.run_it(runner,client)
        self.assertFalse(client.starts)

    def test_stop_after_charged_fight_verifies_it_and_never_starts_another(self):
        client=Client();runner=self.runner(client,battle_limit=5)
        runner._drive_completion.side_effect=lambda *_:client.finish(stop=runner.stop)
        self.run_it(runner,client)
        self.assertEqual(client.starts,[101])
        self.assertIsNone(AdventureRunState(client.credentials.user_id,self.path).read())
        self.assertTrue(any(event['kind']=='adventure_completion_verified' for event in self.events))

    def test_accepted_missing_state_is_never_replayed_in_browser_or_on_resume(self):
        client=Client();client.reply={'result':True};runner=self.runner(client)
        with patch('atcommander.adventure_runner.browser_call_profile',side_effect=AssertionError('No replay')):
            self.run_it(runner,client)
            self.run_it(self.runner(client),client)
        self.assertEqual(client.starts,[101])
        self.assertIsNotNone(AdventureRunState(client.credentials.user_id,self.path).read())

    def test_transport_timeout_keeps_pending_start_and_blocks_resume(self):
        client=Client();client.error=ATApiError('Synthetic timeout');runner=self.runner(client)
        with self.assertRaises(ATApiError):self.run_it(runner,client)
        client.error=None
        self.run_it(self.runner(client),client)
        self.assertEqual(client.starts,[101])

    def test_explicit_rejection_without_battle_clears_only_the_refused_intent(self):
        client=Client();client.reply={'result':False};runner=self.runner(client)
        self.run_it(runner,client)
        self.assertEqual(client.starts,[101])
        self.assertIsNone(AdventureRunState(client.credentials.user_id,self.path).read())

    def test_contradictory_rejection_with_battle_stays_pending(self):
        client=Client();client.reply={'result':False,'battle_data':{'battle_id':'synthetic-battle','host_id':'synthetic-host'}}
        self.run_it(self.runner(client),client)
        self.run_it(self.runner(client),client)
        self.assertEqual(client.starts,[101])

    def test_uncredited_win_pauses_and_does_not_report_completion_or_spend_again(self):
        client=Client();runner=self.runner(client,battle_limit=5)
        runner._drive_completion.side_effect=lambda *_:client.finish(credit=False)
        self.run_it(runner,client)
        self.run_it(self.runner(client),client)
        self.assertEqual(client.starts,[101])
        self.assertFalse(any(event['kind']=='adventure_completion_verified' for event in self.events))

    def test_three_confirmed_losses_pause_before_a_fourth_start(self):
        client=Client();runner=self.runner(client,battle_limit=10)
        runner._drive_completion.side_effect=lambda *_:client.finish(won=False)
        self.run_it(runner,client)
        self.assertEqual(client.starts,[101]*3)
        self.assertIsNone(AdventureRunState(client.credentials.user_id,self.path).read())

    def test_missing_accepted_host_preserves_pending_without_card_play(self):
        client=Client();client.reply={'result':True,'battle_data':{'battle_id':'synthetic-battle'}}
        runner=self.runner(client);self.run_it(runner,client)
        runner._drive_completion.assert_not_called()
        self.assertIsNotNone(AdventureRunState(client.credentials.user_id,self.path).read())


class AdventureJournalTests(unittest.TestCase):
    def test_empty_incomplete_unknown_phase_and_wrong_owner_cannot_authorize_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'pending.json';state=AdventureRunState('synthetic-owner',path)
            for pending in ({},{'phase':'unknown'},{'phase':'start_pending','mission_id':101}):
                with self.subTest(pending=pending):
                    path.write_text(json.dumps({'schema':1,'owner':state.owner,'pending':pending}),encoding='utf-8')
                    with self.assertRaises(RuntimeError):state.read()
            path.write_text(json.dumps({'schema':1,'owner':'other','pending':None}),encoding='utf-8')
            with self.assertRaises(RuntimeError):state.read()

    def test_missing_pending_property_and_premature_outcome_cannot_clear_an_intent(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'pending.json';state=AdventureRunState('fixture',path)
            path.write_text(json.dumps({'schema':1,'owner':state.owner}),encoding='utf-8')
            with self.assertRaises(RuntimeError):state.read()
            valid={'phase':'start_pending','mission_id':101,'campaign_id':'1','wins_before':0,'cost':2,'server_time':1000,'farming':False}
            for changed in ({'terminal':False},{'phase':'terminal'},{'request':{'password':'synthetic-private'}}):
                with self.subTest(changed=changed),self.assertRaises(RuntimeError):state.save({**valid,**changed})


class AdventurePreflightStopTests(unittest.TestCase):
    def test_stop_before_or_during_boost_read_cannot_open_rewarded_ad_flow(self):
        for initial_stop in (True,False):
            stopped=[initial_stop]
            def boost():
                stopped[0]=True
                return {'boost_level':0}
            client=SimpleNamespace(get_boost_level=Mock(side_effect=boost))
            with self.subTest(initial_stop=initial_stop),patch('atcommander.ad_ui.attempt_legitimate_ad_ui') as ads:
                self.assertEqual(ad_preflight(client,auto_watch=True,should_stop=lambda:stopped[0]),'stopped')
                ads.assert_not_called()
            self.assertEqual(client.get_boost_level.call_count,0 if initial_stop else 1)


class AdventureMoveSafetyTests(unittest.TestCase):
    def test_unchanged_state_or_identity_drift_never_repeats_a_move(self):
        initial={'battle_id':'fixture-battle','host_id':'fixture-host','auto_play':[10]}
        for state in (initial,{**initial,'battle_id':'different'}, {**initial,'host_id':'different'}):
            with self.subTest(state=state),tempfile.TemporaryDirectory() as directory:
                runner=AdventureRunner(101,complete_islands=True,state_path=Path(directory)/'pending.json')
                runner._journal=AdventureRunState('fixture',runner.state_path)
                runner._pending={'phase':'battle_ready','mission_id':101,'campaign_id':'1','wins_before':0,'cost':2,'server_time':1000,'farming':False}
                runner._client=SimpleNamespace(play_battlefield_card=Mock(return_value={'result':True,'battle_data':state}),
                                               battle_finished=ATClient.battle_finished,extract_outcome=ATClient.extract_outcome)
                with self.assertRaises(ATApiError):runner._drive_completion(initial,1)
                self.assertEqual(runner._client.play_battlefield_card.call_count,1)
                self.assertEqual(runner._journal.read()['phase'],'move_pending')


if __name__=='__main__':unittest.main()
