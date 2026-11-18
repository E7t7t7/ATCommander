import copy
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from atcommander.event_priority import EventPriority,checkpoint,restore_selection
from atcommander.command_center import CommandCenterMixin
from atcommander.remote_center import RemoteCenterMixin
from atcommander.remote_control import EventTimers
from atcommander.command_store import CommandStore
from atcommander.temporary_daily_hero import exact_active_commander


def until(test):
    end=time.monotonic()+3
    while time.monotonic()<end:
        if test():return
        time.sleep(.005)
    raise AssertionError('worker did not reach the expected boundary')


def account(active='20',hero='1001',units=('1','2')):
    return {'result':True,'user_data':{'active_deck':active},'user_decks':{
        '20':{'deck_id':'20','commander_id':hero,'units':{x:{'unit_index':x}for x in units}},
        '21':{'deck_id':'21','commander_id':'1002','units':{'3':{'unit_index':'3'}}}}}

class GateTests(unittest.TestCase):
    def test_hold_acknowledged_only_at_checkpoint_and_resume_keeps_position(self):
        gate=EventPriority();stop=threading.Event();worker=SimpleNamespace(event_priority=gate,priority_owner='arena_runner');position=[]
        gate.request();thread=threading.Thread(target=lambda:position.append(checkpoint(worker,stop)));thread.start()
        until(lambda:gate.parked('arena_runner'));self.assertEqual(position,[])
        gate.release();thread.join(2);self.assertEqual(position,[True]);self.assertFalse(gate.parked('arena_runner'))
    def test_stop_while_held_never_resumes_work(self):
        gate=EventPriority();stop=threading.Event();answer=[];gate.request()
        thread=threading.Thread(target=lambda:answer.append(gate.checkpoint('commands',stop)));thread.start()
        until(lambda:gate.parked('commands'));stop.set();thread.join(2)
        self.assertEqual(answer,[False]);self.assertFalse(gate.parked('commands'))
    def test_armed_future_time_does_not_suspend(self):
        gate=EventPriority();self.assertTrue(gate.checkpoint('arena_runner',threading.Event()));self.assertFalse(gate.has_parked())
    def test_priority_only_ignores_acknowledged_workers(self):
        gate=EventPriority();gate.request();app=SimpleNamespace(event_priority=gate,arena_runner=SimpleNamespace(running=True))
        self.assertTrue(CommandCenterMixin._browser_action_busy(app,priority=True))
        stop=threading.Event();thread=threading.Thread(target=lambda:gate.checkpoint('arena_runner',stop));thread.start()
        try:
            until(lambda:gate.parked('arena_runner'))
            self.assertFalse(CommandCenterMixin._browser_action_busy(app,priority=True))
            self.assertTrue(CommandCenterMixin._browser_action_busy(app))
            app.deck_builder_thread=Mock();app.deck_builder_thread.is_alive.return_value=True
            self.assertTrue(CommandCenterMixin._browser_action_busy(app,priority=True))
        finally:stop.set();gate.release();thread.join(2)
    def test_resume_callback_only_after_actual_hold(self):
        gate=EventPriority();callback=Mock();stop=threading.Event();gate.checkpoint('commands',stop,callback);callback.assert_not_called()
        gate.request();thread=threading.Thread(target=lambda:gate.checkpoint('commands',stop,callback));thread.start()
        until(lambda:gate.parked('commands'));gate.release();thread.join(2);callback.assert_called_once()

class RestoreTests(unittest.TestCase):
    def client(self,live):
        client=Mock();client.init.side_effect=lambda:copy.deepcopy(live);client.get_exact_decks.side_effect=lambda data:data
        def apply(deck_id,hero_id):live['user_data']['active_deck']=deck_id;live['user_decks'][deck_id]['commander_id']=hero_id
        client.apply_selection.side_effect=apply
        return client
    def test_restore_active_deck_and_hero_before_resuming(self):
        original=exact_active_commander(account());live=account('21');client=self.client(live)
        restore_selection(client,original);self.assertEqual(exact_active_commander(live),original);client.apply_selection.assert_called_once_with(deck_id='20',hero_id='1001')
    def test_same_deck_changed_hero_is_restored(self):
        original=exact_active_commander(account());live=account(hero='1003');client=self.client(live);restore_selection(client,original)
        self.assertEqual(exact_active_commander(live),original)
    def test_uncertain_accepted_restore_reconciles_without_replay(self):
        original=exact_active_commander(account());live=account('21');client=self.client(live);apply=client.apply_selection.side_effect
        def uncertain(**fields):apply(**fields);raise TimeoutError('private material')
        client.apply_selection.side_effect=uncertain;restore_selection(client,original);self.assertEqual(client.apply_selection.call_count,1)
    def test_unconfirmed_restore_holds_without_retry(self):
        original=exact_active_commander(account());client=self.client(account('21'));client.apply_selection.side_effect=TimeoutError('private')
        with self.assertRaisesRegex(ValueError,'could not be restored'):restore_selection(client,original)
        self.assertEqual(client.apply_selection.call_count,1)
    def test_changed_original_cards_never_overwritten(self):
        original=exact_active_commander(account());live=account('21',units=('4',));client=self.client(live)
        with self.assertRaisesRegex(ValueError,'cards changed'):restore_selection(client,original)
        client.apply_selection.assert_not_called()

class TimerPriorityTests(unittest.TestCase):
    def app(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        book=EventTimers(CommandStore(Path(tmp.name)/'state.json'));book.arm('Rumble',30,0)
        app=SimpleNamespace(_closing=False,discord_connection=None,event_priority=EventPriority(),event_timers=book,remote_job=None,_orders_ready=Mock(return_value=True),_timers_render=Mock(),_remote_notify=Mock())
        app._remote_begin_event=Mock(side_effect=lambda mode,scheduled:setattr(app,'remote_job',{'mode':mode,'scheduled':scheduled,'battles':0,'results':[]}))
        return app
    def test_due_timer_waits_for_current_battle_then_starts_without_waiting_for_whole_session(self):
        app=self.app();app.arena_runner=SimpleNamespace(running=True);stop=threading.Event()
        RemoteCenterMixin._remote_tick(app);self.assertTrue(app.event_priority.requested);app._remote_begin_event.assert_not_called()
        thread=threading.Thread(target=lambda:app.event_priority.checkpoint('arena_runner',stop));thread.start()
        try:
            until(lambda:app.event_priority.parked('arena_runner'));RemoteCenterMixin._remote_tick(app)
            app._remote_begin_event.assert_called_once_with('Rumble',scheduled=True)
            RemoteCenterMixin._remote_observe(app,{'kind':'finished','mode':'Rumble'})
            thread.join(2);self.assertFalse(thread.is_alive());self.assertFalse(app.event_priority.requested)
        finally:stop.set();app.event_priority.release();thread.join(2)
    def test_future_timer_does_not_claim_priority(self):
        app=self.app();app.event_timers.rows['Rumble']['due']=time.time()+3600;RemoteCenterMixin._remote_tick(app)
        self.assertFalse(app.event_priority.requested);app._remote_begin_event.assert_not_called()
    def test_missing_orders_do_not_hold_regular_tasks_forever(self):
        app=self.app();app.event_timers.disarm('Rumble');app.event_timers.arm('Siege',30,0,weekday=None);app._orders_ready.return_value=False
        RemoteCenterMixin._remote_tick(app);self.assertFalse(app.event_priority.requested)
    def test_both_due_events_run_before_regular_tasks_resume(self):
        app=self.app();app.event_timers.arm('Siege',30,0,weekday=None)
        RemoteCenterMixin._remote_tick(app);self.assertEqual(app.remote_job['mode'],'Rumble')
        RemoteCenterMixin._remote_observe(app,{'kind':'finished','mode':'Rumble'});self.assertTrue(app.event_priority.requested)
        RemoteCenterMixin._remote_tick(app);self.assertEqual(app.remote_job['mode'],'Siege')
        RemoteCenterMixin._remote_observe(app,{'kind':'finished','mode':'Siege'});self.assertFalse(app.event_priority.requested)
    def test_fatal_event_holds_regular_tasks_and_never_auto_replays(self):
        app=self.app();RemoteCenterMixin._remote_tick(app);RemoteCenterMixin._remote_observe(app,{'kind':'fatal','mode':'Rumble'})
        self.assertTrue(app.event_priority.blocked);RemoteCenterMixin._remote_tick(app);self.assertEqual(app._remote_begin_event.call_count,1)
    def test_regular_task_fatal_during_handoff_blocks_event_start(self):
        app=self.app();app.event_priority.request();RemoteCenterMixin._remote_observe(app,{'kind':'fatal','mode':'Arena'})
        RemoteCenterMixin._remote_tick(app);self.assertTrue(app.event_priority.blocked);app._remote_begin_event.assert_not_called()

class RunnerBoundaryTests(unittest.TestCase):
    def test_arena_finishes_current_fight_then_yields_and_keeps_total_limit(self):
        from atcommander.arena_runner import ArenaRunner
        gate=EventPriority();first=threading.Event();finish=threading.Event();events=[]
        client=Mock();client.init.return_value={'user_data':{'stamina':10}};client.extract_battle_data.return_value={'battle_id':'fixture'};client.opponent_name.return_value='Fixture'
        with patch('atcommander.arena_runner.ATClient',return_value=client),patch('atcommander.arena_runner.load_credentials',return_value=SimpleNamespace(ready=True)),patch('atcommander.arena_runner.LifetimeTracker'):
            runner=ArenaRunner(battle_limit=2,between_battles_delay=.1,event=events.append);runner.event_priority=gate;runner.priority_owner='arena_runner';runner._capture_snapshot=Mock(return_value={});runner._ad_preflight=Mock();runner.tracker=Mock()
            def drive(*args):
                if client.start_arena_battle.call_count==1:first.set();self.assertTrue(finish.wait(3))
                return True,2,{},{}
            runner._drive_one=drive;runner.start()
            try:
                self.assertTrue(first.wait(3));gate.request();self.assertFalse(gate.parked('arena_runner'));finish.set()
                until(lambda:gate.parked('arena_runner'));self.assertEqual(client.start_arena_battle.call_count,1)
                gate.release();runner._thread.join(3);self.assertFalse(runner.running);self.assertEqual(client.start_arena_battle.call_count,2)
                self.assertEqual([e['number']for e in events if e['kind']=='battle_end'],[1,2])
            finally:finish.set();runner.stop();gate.release();runner._thread.join(3)
    def test_adventure_mastery_keeps_route_count_and_energy_budget(self):
        from atcommander.adventure_runner import AdventureRunner
        from test_adventure_completion_runner import Client
        with tempfile.TemporaryDirectory()as tmp:
            client=Client(energy=100,wins=11);events=[];gate=EventPriority();first=threading.Event();finish=threading.Event()
            runner=AdventureRunner(101,battle_limit=2,complete_islands=True,state_path=Path(tmp)/'pending.json',energy_budget=5,between_battles_delay=.1,event=events.append)
            runner._client=client;runner.event_priority=gate;runner.priority_owner='adventure_runner'
            def drive(*args):
                if len(client.starts)==1:first.set();self.assertTrue(finish.wait(3))
                return client.finish()
            runner._drive_completion=drive
            with patch('atcommander.adventure_runner.ad_preflight'):
                thread=threading.Thread(target=lambda:runner._run_completion(client.init()));thread.start()
                try:
                    self.assertTrue(first.wait(3));gate.request();self.assertFalse(gate.parked('adventure_runner'));finish.set()
                    until(lambda:gate.parked('adventure_runner'));self.assertEqual(client.starts,[101]);gate.release();thread.join(3)
                    self.assertFalse(thread.is_alive());self.assertEqual(client.starts,[101,102]);self.assertEqual(events[-1]['energy_spent'],5)
                finally:finish.set();runner.stop();gate.release();thread.join(3)
    def test_event_terminal_waits_for_restore_and_error_is_fatal(self):
        from atcommander.event_runner import EventBattleRunner
        with patch('atcommander.event_runner.LifetimeTracker'):
            events=[];runner=EventBattleRunner('Rumble',event=events.append);runner.priority_restore=True;runner._priority_original=object();runner._client=Mock()
            with patch('atcommander.event_priority.restore_selection')as restore:
                runner._emit('finished',stopped=False);restore.assert_called_once();self.assertTrue(events[-1]['priority_restored'])
            with patch('atcommander.event_priority.restore_selection',side_effect=ValueError('private')):
                runner._emit('finished',stopped=False);self.assertEqual(events[-1]['kind'],'fatal');self.assertNotIn('private',events[-1]['message'])

class ServicePriorityTests(unittest.TestCase):
    def test_research_finishes_verified_request_then_holds_and_keeps_budget(self):
        from test_research_automation import FakeService
        from atcommander.research_automation import ResearchAutomation,ResearchOptions
        with tempfile.TemporaryDirectory()as tmp:
            service=FakeService();gate=EventPriority();service.event_priority=gate;service.priority_owner='commands'
            original_call=service.call
            def call(message,data=None):
                result=original_call(message,data)
                if message=='researchCombo':gate.request()
                return result
            service.call=call;engine=ResearchAutomation(service,Path(tmp)/'research.json');answers=[]
            thread=threading.Thread(target=lambda:answers.append(engine.run(ResearchOptions(ads=False,speedups=True,max_speedup_hours=2),wait=False,max_actions=6)))
            thread.start()
            try:
                until(lambda:gate.parked('commands'))
                self.assertEqual([name for name,_ in service.mutations()],['researchCombo'])
                self.assertIsNone(engine.state['pending_action'])
                # Only the first request should trigger this simulated event.
                service.call=original_call;gate.release();thread.join(3)
                self.assertFalse(thread.is_alive());self.assertEqual(answers[0].data['completed'],2)
                self.assertEqual(answers[0].data['speedups_used_hours'],2)
                self.assertEqual([name for name,_ in service.mutations()],['researchCombo','useResearchSpeedUpItem','completeResearch']*2)
            finally:service.stop.set();gate.release();thread.join(3)
    def test_parked_research_command_and_worker_do_not_block_scheduled_event(self):
        gate=EventPriority();gate.request();stop=threading.Event();worker=Mock();worker.is_alive.return_value=True
        app=SimpleNamespace(event_priority=gate,command_busy=True,command_mutating=True,research_busy=True,research_thread=worker,_priority_launch=True)
        thread=threading.Thread(target=lambda:gate.checkpoint('commands',stop));thread.start()
        try:
            until(lambda:gate.parked('commands'))
            self.assertFalse(CommandCenterMixin._browser_action_busy(app,priority=True))
            self.assertFalse(CommandCenterMixin._command_blocks_runner(app,ignore_ads=True))
        finally:stop.set();gate.release();thread.join(2)
    def test_suspended_event_reference_and_phone_results_restored_before_release(self):
        from atcommander.event_priority import release_tasks
        gate=EventPriority();gate.request();old=object();job={'mode':'Challenge','battles':2}
        app=SimpleNamespace(event_priority=gate,event_runner=object(),_priority_suspended_event=old,remote_job=None,_priority_suspended_job=job)
        release_tasks(app);self.assertIs(app.event_runner,old);self.assertIs(app.remote_job,job);self.assertFalse(gate.requested)
    def test_same_mode_result_from_another_worker_cannot_complete_scheduled_job(self):
        gate=EventPriority();gate.request();job={'mode':'Rumble','scheduled':True,'battles':0,'results':[],'runner_key':10}
        app=SimpleNamespace(event_priority=gate,remote_job=job,event_timers=Mock(),_remote_notify=Mock(),_timers_render=Mock())
        RemoteCenterMixin._remote_observe(app,{'kind':'finished','mode':'Rumble','runner_key':11})
        self.assertIs(app.remote_job,job);self.assertTrue(gate.requested);app.event_timers.finish.assert_not_called()

class InterruptedHandoffTests(unittest.TestCase):
    def app(self):
        gate=EventPriority();gate.request()
        return SimpleNamespace(event_priority=gate,remote_job={'mode':'Rumble','scheduled':True,'battles':0,'results':[]},event_timers=Mock(),_timers_render=Mock(),_remote_notify=Mock())
    def test_stop_event_alone_does_not_resume_regular_tasks_with_unrestored_deck(self):
        app=self.app();RemoteCenterMixin._remote_observe(app,{'kind':'finished','mode':'Rumble','stopped':True})
        self.assertTrue(app.event_priority.blocked);app.event_timers.finish.assert_called_once_with('Rumble',failed=True)
    def test_failed_routine_cannot_release_an_uncertain_battle_into_scheduled_event(self):
        app=self.app();app.remote_job=None;RemoteCenterMixin._remote_observe(app,{'kind':'command_result','ok':False})
        self.assertTrue(app.event_priority.blocked)
