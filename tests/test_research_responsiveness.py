import gc
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest

from atcommander.research_automation import ResearchAutomation, ResearchOptions
from atcommander.research_center import ResearchCenterMixin
from atcommander.ui_runtime import MainThreadRuntime
from test_research_automation import FakeService


def large_service(count=6000):
    service=FakeService()
    service.cards={'90':{'name':'Synthetic Combo','rarity':4}}
    service.recipes={}
    inputs=set()
    for i in range(count):
        first=1000+i//1000;second=2000+i%1000
        inputs.update((first,second));service.recipes[f'{first}~{second}']='90|synthetic'
    for cid in inputs:service.cards[str(cid)]={'name':f'Card {cid}','rarity':4}
    service.live['user_units']={str(i):{'unit_id':cid,'unit_index':i} for i,cid in enumerate(inputs,1)}
    return service


class CancellableRecipes(dict):
    def __init__(self, source, stop):
        super().__init__(source);self.stop=stop;self.read_count=0
    def items(self):
        for item in super().items():
            self.read_count+=1
            if self.read_count==260:self.stop.set()
            yield item


class ResearchResponsivenessTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'queue.json'

    def test_stop_during_large_initial_mapping_does_not_save_or_mutate(self):
        service=large_service(20000);recipes=CancellableRecipes(service.recipes,service.stop);service.recipes=recipes
        answer=ResearchAutomation(service,self.path).run(ResearchOptions(ads=False),wait=False)
        self.assertEqual(answer.data['status'],'paused')
        self.assertLessEqual(recipes.read_count,512)
        self.assertFalse(self.path.exists());self.assertEqual(service.mutations(),[])

    def test_preview_mapping_can_be_cancelled_without_exception_or_mutation(self):
        service=large_service(20000);recipes=CancellableRecipes(service.recipes,service.stop);service.recipes=recipes
        answer=ResearchAutomation(service,self.path).preview(ResearchOptions(ads=False))
        self.assertEqual(answer.data['status'],'paused');self.assertLessEqual(recipes.read_count,512)
        self.assertFalse(self.path.exists());self.assertEqual(service.mutations(),[])

    def test_large_preview_keeps_total_count_but_bounds_returned_rows(self):
        service=large_service()
        answer=ResearchAutomation(service,self.path).preview(ResearchOptions(ads=False,max_pairs=100000))
        self.assertEqual(answer.data['count'],6000);self.assertEqual(answer.data['matching_count'],6000)
        self.assertEqual(len(answer.rows),5000);self.assertTrue(answer.data['rows_truncated'])
        self.assertEqual(service.mutations(),[])

    def test_large_saved_queue_keeps_all_pairs_despite_display_limit(self):
        import json
        service=large_service()
        answer=ResearchAutomation(service,self.path).run(ResearchOptions(ads=False,max_pairs=100000),wait=False,max_actions=0)
        self.assertEqual(answer.data['count'],6000);self.assertEqual(len(answer.rows),5000)
        saved=json.loads(self.path.read_text())['research']
        self.assertEqual(len(saved['queue']),6000);self.assertEqual(service.mutations(),[])

    def test_done_lookup_does_not_scan_the_whole_queue(self):
        service=FakeService();engine=ResearchAutomation(service,self.path)
        row={'pair':'22~23','status':'running'}
        class NoScanning(list):
            def __iter__(self):raise AssertionError('quadratic queue scan')
        engine.state={'queue':NoScanning([row]),'completed':0};engine._queue_by_pair={'22~23':row}
        engine._mark_done('22~23');engine._mark_done('22~23')
        self.assertEqual(row['status'],'completed');self.assertEqual(engine.state['completed'],1)


class Scheduler:
    def __init__(self):self.pending={};self.sequence=0
    def after(self, delay, callback):
        self.sequence+=1;self.pending[self.sequence]=callback;return self.sequence
    def after_cancel(self, identity):self.pending.pop(identity,None)
    def step(self):
        identity=min(self.pending);callback=self.pending.pop(identity);callback()


class Table:
    def __init__(self, old=0):self.rows=list(range(old));self.inserted=0;self.max_deletion=0
    def get_children(self):return tuple(self.rows)
    def delete(self,*batch):
        self.max_deletion=max(self.max_deletion,len(batch));self.rows=[r for r in self.rows if r not in set(batch)]
    def insert(self,*args,**kwargs):self.inserted+=1;self.rows.append(f'new{self.inserted}')


class Renderer(ResearchCenterMixin):
    def __init__(self, old=0):
        self.root=Scheduler();self.research_tree=Table(old);self._closing=False
        self.research_render_generation=0;self.research_render_after=None
        self.command_stop=threading.Event();self.research_busy=True
        self.research_status=SimpleNamespace(set=lambda text:None)


class RenderResponsivenessTests(unittest.TestCase):
    def test_large_render_yields_after_fifty_rows_and_pause_cancels_remainder(self):
        renderer=Renderer();renderer._research_render_rows([{'name':'Combo'}]*5000)
        self.assertEqual(renderer.research_tree.inserted,50);self.assertEqual(len(renderer.root.pending),1)
        renderer._research_pause()
        self.assertTrue(renderer.command_stop.is_set());self.assertFalse(renderer.root.pending)
        self.assertEqual(renderer.research_tree.inserted,50)

    def test_clear_large_previous_table_is_chunked_before_insert(self):
        renderer=Renderer(old=5000);renderer._research_render_rows([{'name':'New'}]*2)
        self.assertEqual(len(renderer.research_tree.rows),4900);self.assertEqual(renderer.research_tree.inserted,0)
        while renderer.root.pending:renderer.root.step()
        self.assertEqual(renderer.research_tree.inserted,2);self.assertEqual(renderer.research_tree.max_deletion,100)

    def test_superseded_callback_cannot_insert_stale_rows(self):
        renderer=Renderer();renderer._research_render_rows([{'name':'Old'}]*100)
        stale=next(iter(renderer.root.pending.values()))
        renderer._research_render_rows([{'name':'New'}]*2);stale()
        while renderer.root.pending:renderer.root.step()
        self.assertEqual(renderer.research_tree.inserted,52)
        self.assertEqual(len(renderer.research_tree.rows),2)


class RuntimeTests(unittest.TestCase):
    def test_worker_allocations_cannot_collect_gui_cycles(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        runtime=MainThreadRuntime(Path(temp.name)/'crash.log');self.addCleanup(runtime.close)
        owner=threading.get_ident();finalized=[]
        class Cycle:
            def __del__(self):finalized.append(threading.get_ident())
        cycle=Cycle();cycle.self=cycle;cycle=None
        def allocate():
            garbage=[{str(i):[i]} for i in range(10000)]
            self.assertFalse(gc.isenabled())
        worker=threading.Thread(target=allocate);worker.start();worker.join()
        self.assertEqual(finalized,[])
        runtime.collect();self.assertEqual(finalized,[owner])

    def test_crash_record_has_source_positions_without_exception_data(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        path=Path(temp.name)/'crash.log';runtime=MainThreadRuntime(path);self.addCleanup(runtime.close)
        try:raise RuntimeError('login_hash=private_value clisig=secret_signature')
        except RuntimeError as error:runtime.record_exception(type(error),error.__traceback__)
        text=path.read_text();self.assertIn('RuntimeError',text);self.assertIn('test_crash_record',text)
        self.assertNotIn('private_value',text);self.assertNotIn('secret_signature',text)


class EventLoopTests(unittest.TestCase):
    def test_worker_event_flood_yields_for_stop_input(self):
        import queue
        from atcommander.app import ATCommanderApp
        pending=queue.Queue();handled=[];root=Scheduler()
        for i in range(1000):pending.put({'sequence':i})
        app=SimpleNamespace(events=pending,root=root,_handle_event=lambda event:handled.append(event['sequence']),_drain_events=lambda:None)
        ATCommanderApp._drain_events(app)
        self.assertEqual(len(handled),50);self.assertEqual(pending.qsize(),950)
        self.assertEqual(len(root.pending),1)

    def test_one_bad_callback_does_not_stop_worker_event_processing(self):
        import queue
        from atcommander.app import ATCommanderApp
        pending=queue.Queue();pending.put({'bad':True});pending.put({'good':True})
        handled=[];reported=[];root=Scheduler()
        root.report_callback_exception=lambda category,error,tb:reported.append(category.__name__)
        def handler(event):
            if event.get('bad'):raise ValueError('private runtime content')
            handled.append(event)
        app=SimpleNamespace(events=pending,root=root,_handle_event=handler,_drain_events=lambda:None)
        ATCommanderApp._drain_events(app)
        self.assertEqual(reported,['ValueError']);self.assertEqual(handled,[{'good':True}]);self.assertEqual(len(root.pending),1)

    def test_closing_waits_for_research_thread_and_completed_result(self):
        from atcommander.app import ATCommanderApp
        from unittest.mock import patch
        root=Scheduler();destroyed=[];stopped=[];root.title=lambda text:None;root.destroy=lambda:destroyed.append(True)
        worker=SimpleNamespace(is_alive=lambda:True)
        app=SimpleNamespace(_closing=False,root=root,research_busy=True,command_busy=True,research_thread=worker,
            arena_runner=None,event_runner=None,adventure_runner=None,swole_runner=None,stop_all=lambda:stopped.append(True))
        with patch("atcommander.embedded_browser.close_owned_browser",
                   return_value={"embedded":False,"reason":"browser_closed"}), \
             patch("atcommander.embedded_browser.configure"), \
             patch("atcommander.ad_browser_focus.release_all_game_input_focus"):
            ATCommanderApp.close_app(app)
            self.assertEqual(stopped,[True]);self.assertFalse(destroyed)
            app.research_busy=False;app.command_busy=False
            root.step();self.assertFalse(destroyed)
            worker.is_alive=lambda:False;root.step();self.assertEqual(destroyed,[True])
