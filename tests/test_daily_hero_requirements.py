import unittest

from atcommander.daily_hero_requirements import commander_ip_requirement, plan_commander_ip


class DailyHeroRequirementTests(unittest.TestCase):
    def test_requirement_uses_real_schema_and_not_name(self):
        self.assertEqual(commander_ip_requirement({'name': 'Any name', 'req': {'commander_ip': '4'}}), 4)
        self.assertIsNone(commander_ip_requirement({'name': 'Daily - King of the Hill Takeover'}))
        self.assertIsNone(commander_ip_requirement({'req': {'commander_ip': True}}))

    def test_exact_static_type_and_live_unlock_join(self):
        squad = {'user_hero_squad': [{'hero_id': 1001, 'unlocked': 1, 'level': 15},
                                     {'hero_id': 4001, 'unlocked': 1, 'level': 15},
                                     {'hero_id': 4002, 'unlocked': 0, 'level': 20}]}
        cards = {'1001': {'id': 1001, 'name': 'Synthetic King', 'type': 4},
                 '4001': {'id': 4001, 'name': 'Synthetic Other', 'type': 1},
                 '4002': {'id': 4002, 'name': 'Synthetic Locked', 'type': 4}}
        plan = plan_commander_ip('4', squad, cards)
        self.assertEqual([r['hero_id'] for r in plan['candidates']], ['1001'])
        self.assertEqual(plan['selected']['hero_id'], '1001')

    def test_current_matching_hero_is_preserved_before_level_preference(self):
        squad = {'user_hero_squad': {'4001': {'unlocked': '1', 'level': 10},
                                    '4002': {'unlocked': 1, 'level': 15}}}
        catalog = {'4001': {'type': '4', 'name': 'Hank'}, '4002': {'type': 4, 'name': 'Bobby'}}
        self.assertEqual(plan_commander_ip(4, squad, catalog, current_hero_id=4001)['selected']['hero_id'], '4001')
        self.assertEqual(plan_commander_ip(4, squad, catalog)['selected']['hero_id'], '4002')

    def test_training_level_does_not_substitute_for_battle_level(self):
        squad = {'user_hero_squad': [{'hero_id': 4001, 'unlocked': 1, 'level': 10, 'training_level': 0},
                                     {'hero_id': 4002, 'unlocked': 1, 'level': 5, 'training_level': 20}]}
        self.assertEqual(plan_commander_ip(4, squad, {'4001': {'type': 4}, '4002': {'type': 4}})['selected']['hero_id'], '4001')

    def test_missing_conflicting_and_mismatched_catalogs_remain_unresolved(self):
        squad = {'user_hero_squad': [{'hero_id': 4001, 'unlocked': 1},
                                     {'hero_id': 4002, 'unlocked': 1},
                                     {'hero_id': 4003, 'unlocked': 1}]}
        plan = plan_commander_ip(4, squad, {'4001': {'type': 4}, '4002': {'id': 9001, 'type': 4}},
                                {'4001': {'type': 1}, '4003': {'type': True}})
        self.assertIsNone(plan['selected'])
        self.assertEqual({r['hero_id'] for r in plan['unresolved']}, {'4001', '4002', '4003'})

    def test_rejected_missing_or_ambiguous_squad_cannot_select(self):
        for status in (None, {}, {'result': False, 'user_hero_squad': [{'hero_id': 4001, 'unlocked': 1}]}):
            self.assertIsNone(plan_commander_ip(4, status, {'4001': {'type': 4}})['selected'])
        duplicate = {'user_hero_squad': [{'hero_id': 4001, 'unlocked': 1}, {'hero_id': 4001, 'unlocked': 0}]}
        self.assertIsNone(plan_commander_ip(4, duplicate, {'4001': {'type': 4}})['selected'])

    def test_future_or_invalid_ip_is_not_guessed(self):
        for ip in (None, True, 0, 8, 'King of the Hill'):
            self.assertIsNone(plan_commander_ip(ip, {'user_hero_squad': []})['selected'])


if __name__ == '__main__':
    unittest.main()
