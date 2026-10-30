import tempfile,unittest,threading
from pathlib import Path
from copy import deepcopy
from unittest.mock import patch
from atcommander.command_store import CommandStore
from atcommander.local_commands import CommandUnavailable
from atcommander.reward_cleanup import collection_steps,cleanup_earned_rewards
from atcommander.deck_selection import resolve_deck,label_parts,saved_name_key

class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.live={'user_data':{'level':50},'user_collections':[{'id':8,'name':'Earned','unlock_level':2,'milestone_claimed':0,'card_ids_collected':[1,2],'milestone':{'1':{'id':1,'points_req':2},'2':{'id':2,'points_req':3}}}]}
        class Service:
            def __init__(self,path):
                self.store=CommandStore(path);self.calls=[];self.context=self;self.stop=threading.Event();self.emit=None
            def invalidate(self):pass
            def clock(self):return 100
        self.service=Service(Path(self.tmp.name)/'state.json')
        self.read=patch('atcommander.reward_cleanup.reward_state',lambda _:deepcopy(self.live));self.read.start();self.addCleanup(self.read.stop)
    def test_only_next_earned_collection_milestone(self):
        plan=collection_steps(self.live,100)
        self.assertEqual(plan[0]['payload'],{'collection_id':8,'milestone_id':1,'cards':'[]'})
        self.live['user_collections'][0]['milestone_claimed']=1
        self.assertEqual(collection_steps(self.live,100),[])
    def test_future_locked_and_duplicate_cards_not_claimed(self):
        row=self.live['user_collections'][0]
        for key,value in [('release_time',200),('unlock_level',100),('card_ids_collected',[1,1])]:
            original=deepcopy(row);row[key]=value
            self.assertEqual(collection_steps(self.live,100),[])
            row.clear();row.update(original)
    def test_claim_verified_and_not_repeated(self):
        def call(name,payload):
            self.service.calls.append((name,payload));self.live['user_collections'][0]['milestone_claimed']=1
            return {'result':True}
        self.service.call=call
        self.assertEqual(cleanup_earned_rewards(self.service)['verified_advances'],1)
        self.assertEqual(cleanup_earned_rewards(self.service)['confirmed'],0)
        self.assertEqual(len(self.service.calls),1)
    def test_lost_response_reconciles_without_replay(self):
        def call(name,payload):
            self.service.calls.append(name);raise TimeoutError()
        self.service.call=call
        with self.assertRaises(CommandUnavailable):cleanup_earned_rewards(self.service)
        with self.assertRaises(CommandUnavailable):cleanup_earned_rewards(self.service)
        self.assertEqual(len(self.service.calls),1)
        self.live['user_collections'][0]['milestone_claimed']=1
        self.assertEqual(cleanup_earned_rewards(self.service)['confirmed'],0)
        self.assertEqual(len(self.service.calls),1)
    def test_false_success_does_not_replay(self):
        self.service.call=lambda *a:{'result':True}
        with self.assertRaises(CommandUnavailable):cleanup_earned_rewards(self.service)
        self.service.call=lambda *a:self.fail('unconfirmed claim replayed')
        with self.assertRaises(CommandUnavailable):cleanup_earned_rewards(self.service)
    def test_quest_dictionary_keys_remain_authoritative(self):
        from atcommander.reward_cleanup import _source,_advanced
        step={'kind':'quests','payload':{'achievement_id':45}}
        live={'user_achievements':{'45':{'name':'Daily','status':2}}}
        self.assertEqual(_source(live,step)['status'],2)
        self.assertTrue(_advanced(live,step,1))

    def test_acknowledged_quest_disappearing_is_verified(self):
        self.live={'user_achievements':{'45':{'name':'Daily','status':1,'progress':2,'max_progress':2},'46':{'name':'Other','status':0,'progress':0,'max_progress':2}}}
        def call(name,payload):
            self.service.calls.append(name);self.live['user_achievements'].pop(str(payload['achievement_id']))
            return {'result':True}
        self.service.call=call
        self.assertEqual(cleanup_earned_rewards(self.service)['verified_advances'],1)
        self.assertEqual(len(self.service.calls),1)

    def test_stop_spends_nothing(self):
        self.service.stop.set();self.service.call=lambda *a:self.fail('stopped claim sent')
        self.assertEqual(cleanup_earned_rewards(self.service)['confirmed'],0)

class DeckSelectionTests(unittest.TestCase):
    def test_name_resolves_actual_server_key(self):
        rows=[{'id':'20','name':'ArcherFood'},{'id':'24','name':'Disguised Combo'}]
        self.assertEqual(resolve_deck('24','ArcherFood',rows),'20')
        self.assertEqual(resolve_deck('24','Disguised Combo',rows),'24')
    def test_ambiguous_name_fails_closed(self):
        self.assertIsNone(resolve_deck('24','ArcherFood',[{'id':1,'name':'ArcherFood'},{'id':2,'name':'ArcherFood'}]))
    def test_intentional_rename_keeps_id(self):
        self.assertEqual(resolve_deck('20','Old',[{'id':20,'name':'New'}]),'20')
        self.assertEqual(label_parts('ArcherFood  [ID 20]'),('20','ArcherFood'))
        self.assertEqual(saved_name_key('arena_deck_id'),'arena_deck_name')
