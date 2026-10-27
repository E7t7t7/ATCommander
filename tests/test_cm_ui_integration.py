import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from atcommander.app import ATCommanderApp

class Value:
    def __init__(self,value):self.value=value
    def get(self):return self.value
    def set(self,value):self.value=value

class CMUIIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.app=object.__new__(ATCommanderApp)
        self.app.cm_status=Value("")
        self.app.cm_query=Value("Fixture A")
        self.app.cm_engine=Mock()
        self.app.cm_offers=[{"combo_id":"fixture-a"}]
        self.app.cm_review_rows=[]
        self.app._cm_set_detail=Mock()
        self.callbacks=[];self.workers=[];self.renders=[]
        self.app.root=Mock()
        self.app.root.after.side_effect=lambda delay,callback:self.callbacks.append(callback)
        def render(rows):
            self.app.cm_review_rows=list(rows)
            self.renders.append(list(rows))
        self.app._cm_render_reviews=render
        def thread(*,target,**kwargs):
            return SimpleNamespace(start=lambda:self.workers.append(target))
        self.thread_patch=patch("atcommander.app.threading.Thread",side_effect=thread)
        self.thread_patch.start();self.addCleanup(self.thread_patch.stop)
        self.pool_workers=[]
        worker_counts=self.pool_workers
        class InlinePool:
            def __init__(self,max_workers):worker_counts.append(max_workers)
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def map(self,fn,names):return map(fn,names)
        self.pool_patch=patch("concurrent.futures.ThreadPoolExecutor",new=InlinePool)
        self.pool_patch.start();self.addCleanup(self.pool_patch.stop)

    def reviews(self):
        return [SimpleNamespace(combo_id=str(index),name=f"Fixture {letter}",score=float(5-index),
                                currency="Mastery Stones",cost=1000,community_score=None)
                for index,letter in enumerate("ABCD")]

    def run_callbacks(self):
        while self.callbacks:self.callbacks.pop(0)()

    def test_explicit_shop_action_refreshes_even_when_offers_cached(self):
        self.app._cm_refresh=Mock()
        self.app._cm_review_shop()
        self.app._cm_refresh.assert_called_once_with(auto_shop=True)
        self.app.cm_engine.review_shop.assert_not_called()

    def test_failed_refresh_clears_stale_offers_and_reviews(self):
        self.app.cm_review_rows=self.reviews()
        with patch("atcommander.app.ATClient"),patch("atcommander.app.load_credentials"), \
             patch("atcommander.app.collect_live_review_data",side_effect=RuntimeError("Fixture failure")):
            self.app._cm_refresh(auto_shop=True)
            self.workers.pop(0)()
        self.run_callbacks()
        self.assertIsNone(self.app.cm_engine)
        self.assertEqual([],self.app.cm_offers)
        self.assertEqual([],self.app.cm_review_rows)
        self.assertIn("stale",self.app._cm_set_detail.call_args.args[0])

    def test_older_failed_refresh_cannot_clear_newer_loaded_state(self):
        original=self.app.cm_engine
        with patch("atcommander.app.ATClient"),patch("atcommander.app.load_credentials"), \
             patch("atcommander.app.collect_live_review_data",side_effect=RuntimeError("Fixture failure")):
            self.app._cm_refresh()
            self.workers.pop(0)()
        self.app._cm_refresh_token=object()
        self.run_callbacks()
        self.assertIs(original,self.app.cm_engine)
        self.assertEqual(1,len(self.app.cm_offers))

    def test_late_individual_consensus_cannot_replace_four_shop_rows(self):
        rows=self.reviews();self.app.cm_engine.review_name.return_value=rows[0]
        self.app._cm_review_one()
        self.app.cm_review_rows=list(rows)
        with patch("atcommander.app.fetch_reddit_consensus",return_value=SimpleNamespace(score=6,count=1)):
            self.workers.pop(0)()
        self.run_callbacks()
        self.app.cm_engine.apply_community_consensus.assert_not_called()
        self.assertEqual(4,len(self.app.cm_review_rows))

    def test_shop_consensus_checks_each_combo_without_blocking_ui(self):
        rows=self.reviews();self.app.cm_engine.review_shop.return_value=rows
        def apply(row,consensus):
            row.community_score=consensus.score
            return row
        self.app.cm_engine.apply_community_consensus.side_effect=apply
        self.app._cm_review_shop(use_loaded=True)
        self.assertEqual(4,len(self.app.cm_review_rows))
        self.assertEqual(1,len(self.workers))
        with patch("atcommander.app.fetch_reddit_consensus",return_value=SimpleNamespace(score=6,count=1)) as fetch:
            self.workers.pop(0)()
            self.assertEqual(4,fetch.call_count)
        self.run_callbacks()
        self.assertEqual(4,self.app.cm_engine.apply_community_consensus.call_count)
        self.assertEqual(4,len(self.app.cm_review_rows))
        self.assertEqual([4],self.pool_workers)
        self.assertIn("ratings found for 4",self.app.cm_status.get())

    def test_shop_status_distinguishes_provider_failure_from_missing_evidence(self):
        rows=self.reviews();self.app.cm_engine.review_shop.return_value=rows
        self.app.cm_engine.apply_community_consensus.side_effect=lambda row,consensus:row
        self.app._cm_review_shop(use_loaded=True)
        responses=[SimpleNamespace(score=None,status="unavailable"),SimpleNamespace(score=None,status="no_match"),SimpleNamespace(score=6,status="stale"),SimpleNamespace(score=7,status="matched")]
        with patch("atcommander.app.fetch_reddit_consensus",side_effect=responses):self.workers.pop(0)()
        self.run_callbacks()
        self.assertIn("1 use cached evidence",self.app.cm_status.get())
        self.assertIn("1 Reddit lookup(s) unavailable",self.app.cm_status.get())

    def test_late_shop_consensus_cannot_replace_new_individual_view(self):
        rows=self.reviews();self.app.cm_engine.review_shop.return_value=rows
        self.app._cm_review_shop(use_loaded=True)
        self.app.cm_review_rows=[self.reviews()[0]]
        with patch("atcommander.app.fetch_reddit_consensus",return_value=None):
            self.workers.pop(0)()
        self.run_callbacks()
        self.app.cm_engine.apply_community_consensus.assert_not_called()
        self.assertEqual(1,len(self.app.cm_review_rows))

    def test_pending_live_refresh_invalidates_old_individual_consensus(self):
        rows=self.reviews();self.app.cm_engine.review_name.return_value=rows[0]
        self.app._cm_review_one();self.app._cm_refresh()
        loading=self.app.cm_status.get()
        with patch("atcommander.app.fetch_reddit_consensus",return_value=None):
            self.workers.pop(0)()
        self.run_callbacks()
        self.app.cm_engine.apply_community_consensus.assert_not_called()
        self.assertEqual(loading,self.app.cm_status.get())

    def test_pending_live_refresh_invalidates_old_shop_consensus(self):
        rows=self.reviews();self.app.cm_engine.review_shop.return_value=rows
        self.app._cm_review_shop(use_loaded=True);self.app._cm_refresh(auto_shop=True)
        loading=self.app.cm_status.get()
        with patch("atcommander.app.fetch_reddit_consensus",return_value=None):
            self.workers.pop(0)()
        self.run_callbacks()
        self.app.cm_engine.apply_community_consensus.assert_not_called()
        self.assertEqual(loading,self.app.cm_status.get())

if __name__=="__main__":unittest.main()
