"""Daily requirement and completion checks recovered with the current source."""
import copy
import unittest
from types import SimpleNamespace

from test_automation_profiles import NOW, FakeRunner, account, quest, step
from atcommander.automation_profiles import build_daily_plan, run_daily_profile
from atcommander.local_commands import CommandResult


def combo_deck(live):
    live['user_data']['active_deck']=2
    live['user_decks']={'2':{'commander':{'unit_id':4002},'units':[{'unit_index':1},{'unit_index':2}]}}
    live['user_units']={'1':{'unit_id':10},'2':{'unit_id':20}}
    live['user_combos_compressed']={'10~20':1}
    return live


class RestoredDailyTests(unittest.TestCase):
    def test_aggregate_uses_other_goals_and_claims_without_extra_actions(self):
        live=account(quest('Daily - Nine to Five',{'daily_quests_completed':5},target=5,type=24))
        plan=build_daily_plan(live,now=NOW)
        self.assertFalse(any(row['command'] in {'play','buy-basic','shop-purchase'} for row in plan.steps))
        self.assertEqual(plan.unresolved,[])
        live['user_achievements'][0]['req']['daily_quests_completed']=4
        self.assertEqual(len(build_daily_plan(live,now=NOW).unresolved),1)

    def test_combo_goal_requires_researched_pair_in_active_deck(self):
        live=combo_deck(account(quest('Daily - Play Combos',{'play_combo':{'num':5}},target=5,type=8)))
        catalog={'combo':{'10~20':'30|1'}}
        plan=build_daily_plan(live,now=NOW,catalogs=catalog)
        self.assertEqual(step(plan,'combos')['args']['count'],5)
        self.assertEqual(plan.unresolved,[])
        live['user_decks']['2']['units'].pop()
        plan=build_daily_plan(live,now=NOW,catalogs=catalog)
        self.assertIsNone(step(plan,'combos'))
        self.assertEqual(len(plan.unresolved),1)

    def test_takeover_requires_matching_unlocked_hero(self):
        live=combo_deck(account(quest('Daily - Takeover',{'commander_ip':4},target=1,type=12)))
        live['user_decks']['2']['commander']['unit_id']=5016
        catalog={'card':{'4002':{'name':'Bobby','type':4}},
                 'hero_status':{'user_hero_squad':{'4002':{'unlocked':1,'level':15}}}}
        plan=build_daily_plan(live,now=NOW,catalogs=catalog)
        self.assertEqual(step(plan,'takeover:4')['hero_requirement'],4)
        self.assertEqual(step(plan,'takeover:4')['args']['count'],1)
        catalog['hero_status']['user_hero_squad']['4002']['unlocked']=0
        self.assertIsNone(step(build_daily_plan(live,now=NOW,catalogs=catalog),'takeover:4'))

    def test_normal_arena_finishes_combo_goal_without_extra_battles(self):
        live=combo_deck(account(quest('Daily - Arena',{'total_pvp_battles_num':10},target=10,id=1),
                               quest('Daily - Combos',{'play_combo':{'num':5}},target=5,type=8,id=2)))
        def action(service,name,args):
            if name=='play':
                service.live['user_achievements'][0]['progress']=10
                service.live['user_achievements'][1]['progress']=5
                return CommandResult(rows=[{'won':True} for _ in range(args['count'])])
        service=FakeRunner(live,action);service.catalogs={'combo':{'10~20':'30|1'}}
        result=run_daily_profile(service,{'include_events':False,'research_pairs':0,'watch_ads':False})
        self.assertEqual([args['count'] for name,args in service.calls if name=='play'],[10])
        self.assertEqual(result.data['remaining'],[])

    def test_matching_hero_fight_shares_arena_goal_and_does_not_assign(self):
        live=combo_deck(account(quest('Daily - Hero',{'commander_ip':4},target=1,type=12,id=1),
                               quest('Daily - Arena',{'total_pvp_battles_num':10},target=10,id=2)))
        live['user_decks']['2']['commander']['unit_id']=4503
        def action(service,name,args):
            if name=='play':
                service.live['user_achievements'][0]['progress']=1
                service.live['user_achievements'][1]['progress']+=args['count']
                return CommandResult(rows=[{'won':True} for _ in range(args['count'])])
        service=FakeRunner(live,action)
        service.context=SimpleNamespace(invalidate=lambda:None)
        service.catalogs={'card':{'4503':{'name':'Adult Bobby','type':4}}}
        def read(name,args=None):
            self.assertEqual(name,'getHeroSquad')
            return {'user_hero_squad':{'4503':{'unlocked':1,'level':15}}}
        service.call=read
        result=run_daily_profile(service,{'include_events':False,'research_pairs':0,'watch_ads':False})
        self.assertEqual([args['count'] for name,args in service.calls if name=='play'],[1,9])
        self.assertEqual(result.data['remaining'],[])

    def test_verified_completion_survives_claim_removing_quest(self):
        live=account(quest('Daily - Arena',{'total_pvp_battles_num':1},target=1))
        def action(service,name,args):
            if name=='play':
                service.live['user_achievements'][0]['progress']=1
                return CommandResult(rows=[{'won':True}])
            if name=='reward-claim' and args['kind']=='quests':service.live['user_achievements']=[]
        result=run_daily_profile(FakeRunner(live,action),{'include_events':False,'research_pairs':0,'watch_ads':False})
        self.assertTrue(result.data['quest_coverage'])
        self.assertIn('are complete',result.message)

    def test_unverified_disappearance_does_not_claim_completion(self):
        live=account(quest('Daily - Arena',{'total_pvp_battles_num':1},target=1))
        def action(service,name,args):
            if name=='daily-login':service.live['user_achievements']=[]
        result=run_daily_profile(FakeRunner(live,action),{'include_events':False,'research_pairs':0,'watch_ads':False})
        self.assertFalse(result.data['quest_coverage'])
        self.assertNotIn('are complete',result.message)
