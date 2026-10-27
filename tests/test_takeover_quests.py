from copy import deepcopy
from types import SimpleNamespace
import threading
import unittest

from atcommander.local_commands import CommandInputError, CommandResult, CommandUnavailable
from atcommander.takeover_quests import run_takeover_battles, takeover_status
from tests.test_pvp_challenge import event


def quest(identity=1, ip=4, progress=0):
    return {'id': str(identity), 'name': 'Daily - Show Takeover', 'type': '12',
            'desc': 'Daily - Play a battle with a King of the Hill Hero equipped.',
            'status': 0, 'progress': progress, 'max_progress': '1', 'end_time': 2000,
            'req': {'commander_ip': str(ip)}}


def options(**values):
    return {'mode': 'arena', 'takeover_quest_mode': 'arena', 'count': 10,
            'reserve': 0, 'min_energy': 0, 'deck': None, 'event': None,
            'max_level': None, 'skip': False, **values}


class FakeService:
    def __init__(self, quests=None, hero=4001):
        self.current = {
            'result': True, 'time': 1000,
            'user_data': {'active_deck': 1, 'stamina': 10},
            'user_decks': {'1': {'deck_id': 1, 'commander': {'unit_id': hero},
                                 'units': [{'unit_index': 10}, {'unit_index': 11}]}},
            'user_achievements': quests if quests is not None else [quest()],
            'user_hero_squad': [{'hero_id': 4001, 'unlocked': 1, 'level': 15},
                                {'hero_id': 1001, 'unlocked': 1, 'level': 15}],
        }
        self.cards = {'4001': {'id': 4001, 'type': 4, 'name': 'Hank'},
                      '1001': {'id': 1001, 'type': 1, 'name': 'Peter'}}
        self.context = SimpleNamespace(_catalogs={}, invalidate=lambda: None)
        self.stop = threading.Event()
        self.clock = lambda: 1000
        self.calls = []
        self.plays = []
        self.advance = True
        self.play_error = None
        self.empty_play = False

    def snapshot(self, refresh=False):
        return deepcopy(self.current)

    def catalog(self, name):
        if name != 'card':
            raise AssertionError('Unexpected catalog loader')
        return self.cards

    def call(self, name, args=None):
        self.calls.append((name, deepcopy(args)))
        if name == 'getHeroSquad':
            return {'user_hero_squad': deepcopy(self.current['user_hero_squad'])}
        if name != 'setDeckCommander':
            raise AssertionError('Unexpected request')
        self.current['user_decks'][str(args['deck_id'])]['commander']['unit_id'] = int(args['commander_id'])
        return self.snapshot()

    def execute(self, name, args=None):
        if name == 'deck-select':
            self.calls.append((name, deepcopy(args)))
            self.current['user_data']['active_deck'] = int(args['deck'])
            return CommandResult(message='Synthetic deck selection')
        if name != 'play':
            raise AssertionError('Unexpected nested command')
        self.plays.append(deepcopy(args))
        if self.play_error:
            raise RuntimeError(self.play_error)
        if self.empty_play:
            return CommandResult(message='Synthetic energy stop')
        deck = self.current['user_decks'][str(self.current['user_data']['active_deck'])]
        ip = self.cards[str(deck['commander']['unit_id'])]['type']
        if self.advance:
            for row in self.current['user_achievements']:
                if int(row['req']['commander_ip']) == ip:
                    row['progress'] = 1
                    row['status'] = 1
        self.current['user_data']['stamina'] -= 1
        return CommandResult(rows=[{'battle': 1, 'mode': args['mode'], 'won': False, 'moves': 4}])


class TakeoverNativeSchemaTests(unittest.TestCase):
    def test_verified_native_shape_is_ready_and_does_not_require_win(self):
        answer = takeover_status({'user_achievements': [quest()]}, 1000)
        self.assertEqual(answer['status'], 'ready')
        self.assertEqual(answer['requirements'], [{'commander_ip': 4, 'show': 'King of the Hill',
                                                   'quest_ids': ['1'], 'battles': 1}])
        self.assertIn('a win is not required', answer['message'])

    def test_unknown_or_multi_battle_shapes_remain_unsupported(self):
        for changes in ({'type': 11}, {'max_progress': 5},
                        {'req': {'commander_ip': 4, 'pvp_wins': 1}},
                        {'req': {'hero_requirement': 4001}},
                        {'desc': 'Win a battle with a show hero.'}, {'desc': ''},
                        {'id': None}, {'req': {'commander_ip': 8}}):
            with self.subTest(changes=changes):
                self.assertEqual(takeover_status({'user_achievements': [{**quest(), **changes}]}, 1000)['status'], 'unsupported')

    def test_duplicate_quest_identity_is_ambiguous(self):
        self.assertEqual(takeover_status({'user_achievements': [quest(), quest()]}, 1000)['status'], 'unsupported')

    def test_malformed_scalars_cannot_form_a_ready_schema(self):
        for changes in ({'progress': False}, {'max_progress': True}, {'type': 12.9},
                        {'id': ''}, {'status': 'unknown'}):
            with self.subTest(changes=changes):
                self.assertEqual(takeover_status({'user_achievements': [{**quest(), **changes}]}, 1000)['status'], 'unsupported')

    def test_missing_expiry_or_future_quest_cannot_start(self):
        no_expiry = quest()
        no_expiry.pop('end_time')
        self.assertEqual(takeover_status({'user_achievements': [no_expiry]}, 1000)['status'], 'unavailable')
        self.assertEqual(takeover_status({'user_achievements': [{**quest(), 'start_time': 1100}]}, 1000)['status'], 'no_match')

    def test_child_cannot_extend_or_clear_parent_expiry(self):
        for child_end in (0,3000):
            live={'user_achievements':[{'name':'Takeover','end_time':999,'sub_quests':[{**quest(),'end_time':child_end}]}]}
            self.assertEqual(takeover_status(live,1000)['status'],'no_match')

    def test_child_cannot_start_before_future_parent(self):
        live={'user_achievements':[{'name':'Takeover','start_time':1100,'end_time':2000,'sub_quests':[{**quest(),'start_time':0}]}]}
        self.assertEqual(takeover_status(live,1000)['status'],'no_match')

    def test_malformed_parent_expiry_is_not_repaired_by_child_window(self):
        live={'user_achievements':[{'name':'Takeover','end_time':True,'sub_quests':[quest()]}]}
        self.assertEqual(takeover_status(live,1000)['status'],'unavailable')

    def test_child_own_window_can_identify_a_quest_in_an_untimed_container(self):
        live={'user_achievements':[{'name':'Takeover','sub_quests':[quest()]}]}
        self.assertEqual(takeover_status(live,1000)['status'],'ready')

    def test_unrelated_malformed_sibling_does_not_invalidate_takeover_window(self):
        live = {'user_achievements': [
            {'id': 9, 'name': 'Unrelated quest', 'end_time': True},
            {'name': 'Unrelated container', 'sub_quests': [
                {'id': 10, 'name': 'Unrelated child', 'start_time': False}, quest()
            ]}
        ]}
        self.assertEqual(takeover_status(live, 1000)['status'], 'ready')


class TakeoverRunnerTests(unittest.TestCase):
    def test_current_matching_hero_needs_one_battle_and_no_switch(self):
        service = FakeService()
        answer = run_takeover_battles(service, options())
        self.assertEqual(len(service.plays), 1)
        self.assertEqual(service.calls, [])
        self.assertFalse(answer.rows[0]['won'])
        self.assertEqual(answer.data['takeover_status'], 'complete')
        self.assertEqual(answer.data['hero_restorations'][0]['restoration_status'], 'unchanged')

    def test_overlapping_same_show_quests_need_one_battle(self):
        service = FakeService([quest(), quest(2)])
        answer = run_takeover_battles(service, options())
        self.assertEqual(len(service.plays), 1)
        self.assertEqual(answer.data['takeover_status'], 'complete')

    def test_total_count_limits_all_show_requirements(self):
        service = FakeService([quest(), quest(2, ip=1)])
        answer = run_takeover_battles(service, options(count=1))
        self.assertEqual(len(service.plays), 1)
        self.assertEqual(answer.data['takeover_status'], 'ready')
        self.assertIn('total battle limit', answer.message)

    def test_multiple_shows_restore_original_hero(self):
        service = FakeService([quest(), quest(2, ip=1)])
        answer = run_takeover_battles(service, options(count=2))
        self.assertEqual(len(service.plays), 2)
        self.assertEqual([args['commander_id'] for name, args in service.calls if name == 'setDeckCommander'], ['1001', '4001'])
        self.assertEqual(service.current['user_decks']['1']['commander']['unit_id'], 4001)
        self.assertEqual(answer.data['takeover_status'], 'complete')

    def test_unadvanced_progress_never_repeats_a_fight(self):
        service = FakeService([quest(), quest(2, ip=1)])
        service.advance = False
        answer = run_takeover_battles(service, options())
        self.assertEqual(len(service.plays), 1)
        self.assertIn('fight was not repeated', answer.message)

    def test_nested_play_keeps_reserves_and_clears_repeated_actions(self):
        service = FakeService()
        run_takeover_battles(service, options(count=7, reserve=3, min_energy=5, skip=True))
        nested = service.plays[0]
        self.assertEqual(nested['count'], 1)
        self.assertEqual(nested['reserve'], 3)
        self.assertEqual(nested['min_energy'], 5)
        self.assertEqual(nested['takeover_quest_mode'], '')
        self.assertIsNone(nested['deck'])
        self.assertTrue(nested['skip'])
        for key in ('claim_daily', 'collect_research', 'buy_basic', 'collect_quests'):
            self.assertFalse(nested[key])

    def test_incompatible_extra_actions_fail_before_deck_selection(self):
        for extra in ({'buy_basic': True}, {'claim_daily': True}, {'collect_research': True},
                      {'use_lootcrate': True}, {'collect_quests': True}, {'claim_sfc': True},
                      {'hero_tokens': 'Peter'}):
            with self.subTest(extra=extra):
                service = FakeService()
                with self.assertRaises(CommandInputError):
                    run_takeover_battles(service, options(deck=2, **extra))
                self.assertEqual(service.calls, [])
                self.assertEqual(service.plays, [])

    def test_requested_deck_is_selected_once_and_cleared_for_nested_play(self):
        service = FakeService(hero=1001)
        service.current['user_decks']['2'] = {'deck_id': 2, 'commander': {'unit_id': 4001},
                                             'units': [{'unit_index': 12}]}
        answer = run_takeover_battles(service, options(deck=2))
        self.assertEqual([name for name, _ in service.calls], ['deck-select'])
        self.assertIsNone(service.plays[0]['deck'])
        self.assertEqual(service.current['user_data']['active_deck'], 2)
        self.assertEqual(answer.data['takeover_status'], 'complete')

    def test_battle_exception_still_restores_original_hero(self):
        service = FakeService(hero=1001)
        service.play_error = 'Synthetic battle failure'
        with self.assertRaisesRegex(RuntimeError, 'Synthetic battle failure'):
            run_takeover_battles(service, options())
        self.assertEqual(len(service.plays), 1)
        self.assertEqual(service.current['user_decks']['1']['commander']['unit_id'], 1001)

    def test_reserve_prevents_hero_selection_and_battle(self):
        service = FakeService(hero=1001)
        answer = run_takeover_battles(service, options(reserve=10))
        self.assertEqual(answer.rows, [])
        self.assertEqual(service.calls, [])
        self.assertEqual(service.plays, [])

    def test_missing_verified_eligible_hero_cannot_start(self):
        service = FakeService(hero=1001)
        service.cards.pop('4001')
        with self.assertRaises(CommandUnavailable):
            run_takeover_battles(service, options())
        self.assertEqual(service.calls, [])
        self.assertEqual(service.plays, [])

    def test_pvp_mode_preserves_exact_event_and_reserve(self):
        service = FakeService()
        service.current['active_events'] = [event(energy=2)]
        run_takeover_battles(service, options(mode='pvp-challenge', takeover_quest_mode='pvp-challenge', event=41, reserve=1))
        self.assertEqual(service.plays[0]['mode'], 'pvp-challenge')
        self.assertEqual(service.plays[0]['event'], 41)
        self.assertEqual(service.plays[0]['reserve'], 1)

    def test_empty_play_stops_and_restores(self):
        service = FakeService(hero=1001)
        service.empty_play = True
        answer = run_takeover_battles(service, options())
        self.assertEqual(len(service.plays), 1)
        self.assertEqual(answer.rows, [])
        self.assertEqual(service.current['user_decks']['1']['commander']['unit_id'], 1001)

    def test_already_stopped_run_does_not_select_or_fight(self):
        service = FakeService()
        service.stop.set()
        answer = run_takeover_battles(service, options(deck=2))
        self.assertEqual(answer.rows, [])
        self.assertEqual(service.calls, [])
        self.assertEqual(service.plays, [])


if __name__ == '__main__':
    unittest.main()
