import copy
import threading
import unittest
from types import SimpleNamespace

from atcommander.local_commands import CommandUnavailable
from atcommander.reward_options import bounded_dependency_claims, claim_plan, execute_claim_plan, reward_state


def quest(qid, **extra):
    return {"id":qid,"name":f"Quest {qid}","status":1,"progress":1,"max_progress":1,**extra}


class Service:
    def __init__(self, achievements=None):
        self.live={"user_achievements":achievements if achievements is not None else [quest(1)],"active_choices":[]}
        self.calls=[];self.invalidations=0;self.stop=threading.Event();self.clock=lambda:100
        self.context=SimpleNamespace(invalidate=self.invalidate)
        self.claim=None;self.choose=None;self.read=None

    def invalidate(self):self.invalidations+=1
    def snapshot(self, refresh=False):
        self.calls.append(("snapshot",{}));return copy.deepcopy(self.live)
    def call(self, name, args=None):
        self.calls.append((name,args or {}))
        if name=="getUserAccount":return self.read() if self.read else copy.deepcopy(self.live)
        if self.claim:return self.claim(args["achievement_id"])
        for row in self.live["user_achievements"]:
            if row.get("id")==args["achievement_id"]:row["status"]=2
        return {"result":True}
    def execute(self, name, args=None):
        self.calls.append((name,args or {}))
        if self.choose:return self.choose()
        self.live["active_choices"]=[]
    def catalog(self, name):return {}
    def claimed(self):return [args["achievement_id"] for name,args in self.calls if name=="completeAchievement"]


class DependencyClaimTests(unittest.TestCase):
    def test_new_aggregate_is_claimed_from_a_fresh_state(self):
        service=Service([quest(1),quest(2,status=0,progress=0)])
        def claim(qid):
            service.live["user_achievements"][qid-1]["status"]=2
            if qid==1:service.live["user_achievements"][1].update(progress=1,status=1)
            return {"result":True}
        service.claim=claim
        self.assertEqual(len(bounded_dependency_claims(service)),2)
        self.assertEqual(service.claimed(),[1,2]);self.assertEqual(service.invalidations,3)

    def test_expired_and_incomplete_quests_are_skipped(self):
        service=Service([quest(1,end_time=100),quest(2,progress=0),quest(3,status=2),quest(4,start_time=101)])
        self.assertEqual(bounded_dependency_claims(service),[]);self.assertEqual(service.claimed(),[])

    def test_unchanged_success_stops_without_retry_or_next_claim(self):
        service=Service([quest(1),quest(2)]);service.claim=lambda _: {"result":True}
        with self.assertRaisesRegex(CommandUnavailable,"remained eligible"):bounded_dependency_claims(service)
        self.assertEqual(service.claimed(),[1])

    def test_removed_claim_is_confirmed_by_new_eligibility(self):
        service=Service()
        def claim(_):service.live["user_achievements"]=[];return {"result":True}
        service.claim=claim
        self.assertEqual(len(bounded_dependency_claims(service)),1)

    def test_rejected_claim_stops_without_retry(self):
        for answer in ({"result":False},{"error":"rejected"},{"result":"0"},{}):
            with self.subTest(answer=answer):
                service=Service();service.claim=lambda _,answer=answer:answer
                with self.assertRaises(CommandUnavailable):bounded_dependency_claims(service)
                self.assertEqual(service.claimed(),[1])

    def test_pending_choices_resolve_before_first_claim(self):
        service=Service();service.live["active_choices"]=[{"type":8,"options":[]}]
        bounded_dependency_claims(service)
        names=[name for name,_ in service.calls]
        self.assertLess(names.index("item-choose"),names.index("completeAchievement"))

    def test_pending_choices_resolve_before_a_dependent_claim(self):
        service=Service([quest(1),quest(2)])
        def claim(qid):
            service.live["user_achievements"][qid-1]["status"]=2
            if qid==1:service.live["active_choices"]=[{"type":8,"options":[]}]
            return {"result":True}
        service.claim=claim;bounded_dependency_claims(service)
        names=[name for name,_ in service.calls]
        self.assertLess(names.index("item-choose"),len(names)-1)
        self.assertEqual(service.claimed(),[1,2])

    def test_unconfirmed_choice_blocks_all_claims(self):
        service=Service();service.live["active_choices"]=[{"type":8}];service.choose=lambda:None
        with self.assertRaisesRegex(CommandUnavailable,"choice is still unconfirmed"):bounded_dependency_claims(service)
        self.assertEqual(service.claimed(),[])

    def test_limit_bounds_new_claims(self):
        service=Service([quest(1),quest(2)])
        self.assertEqual(len(bounded_dependency_claims(service,1)),1);self.assertEqual(service.claimed(),[1])
        for value in (0,101,True,"2"):
            with self.assertRaises(CommandUnavailable):bounded_dependency_claims(Service(),value)

    def test_stop_prevents_reads_and_claims(self):
        service=Service();service.stop.set()
        self.assertEqual(bounded_dependency_claims(service),[]);self.assertEqual(service.calls,[])

    def test_missing_fresh_achievement_metadata_is_not_assumed_empty(self):
        service=Service();service.live.pop("user_achievements")
        with self.assertRaisesRegex(CommandUnavailable,"metadata is unavailable"):bounded_dependency_claims(service)
        self.assertEqual(service.claimed(),[])

    def test_malformed_ids_and_progress_never_claim(self):
        bad=[quest(True),quest(1.5),quest(2,progress="1.5"),quest(3,max_progress=True),quest(4,status="broken"),quest(5,end_time="broken")]
        self.assertEqual(claim_plan({"user_achievements":bad},{"quests"},100),[])

    def test_nested_children_inherit_window_and_use_only_their_own_id(self):
        live={"user_achievements":[{"id":500,"start_time":50,"end_time":200,"children":[quest(1),{"progress":1,"max_progress":1,"status":1},{"2":quest(2,end_time=90)}]}]}
        self.assertEqual([step["payload"]["achievement_id"] for step in claim_plan(live,{"quests"},100)],[1])
        self.assertEqual(claim_plan(live,{"quests"},200),[])
        self.assertEqual(claim_plan(live,{"quests"},49),[])

    def test_fresh_reward_reads_reject_nonempty_api_errors(self):
        service=Service();service.read=lambda:{"result":False,"user_achievements":[quest(1)]}
        with self.assertRaises(CommandUnavailable):reward_state(service)
        self.assertEqual(service.claimed(),[])

    def test_general_plan_rejects_nonempty_api_error(self):
        service=Service();service.claim=lambda _:{"result":False,"error":"rejected"}
        with self.assertRaises(CommandUnavailable):execute_claim_plan(service,claim_plan(service.live,{"quests"},100))
        self.assertEqual(service.claimed(),[1])


if __name__=="__main__":unittest.main()
