import copy
import unittest

from atcommander.pvp_challenge import resolve_pvp_challenge


def event(event_id='41', *, challenge_type=2, level=0, energy=2):
    return {'id': event_id, 'name': 'Fixture PvP Challenge', 'type': 6,
            'start_time': 900, 'end_time': 2000,
            'challenge_data': {'id': '7' + event_id, 'challenge_type': challenge_type,
                               'level': level, 'energy': {'current_value': energy, 'max_value': 6, 'battle_cost': 1}}}


class PvPChallengeTests(unittest.TestCase):
    def test_explicit_pvp_event_preserves_inner_id_zero_level_and_energy(self):
        result = resolve_pvp_challenge({'time': 1000, 'active_events': [event()]}, '41')
        self.assertEqual(result['challenge_id'], '741')
        self.assertEqual(result['event_id'], '41')
        self.assertEqual(result['level'], 0)
        self.assertEqual(result['current'], 2)
        self.assertEqual(result['challenge_type'], 2)

    def test_one_pvp_event_is_auto_selected_even_beside_pve_events(self):
        result = resolve_pvp_challenge({'time': 1000, 'active_events': [event('40', challenge_type=1), event('41', challenge_type='2')]})
        self.assertEqual(result['event_id'], '41')

    def test_multiple_pvp_events_require_explicit_selection(self):
        source = {'time': 1000, 'active_events': [event('41'), event('42')]}
        self.assertIsNone(resolve_pvp_challenge(source))
        self.assertEqual(resolve_pvp_challenge(source, '42')['event_id'], '42')

    def test_pve_missing_marker_name_and_other_modes_never_qualify(self):
        for marker in (1, 3, 5, None, True, 'pvp', '2.0'):
            with self.subTest(marker=marker):
                self.assertIsNone(resolve_pvp_challenge({'time': 1000, 'active_events': [event(challenge_type=marker)]}))

    def test_rejected_upcoming_ended_or_missing_fight_data_is_unavailable(self):
        variants = [
            {'result': False, 'time': 1000, 'active_events': [event()]},
            {'time': 1000, 'upcoming_events': [event()]},
            {'time': 2000, 'active_events': [event()]},
            {'time': 800, 'active_events': [event()]},
            {'active_events': [event()]},
        ]
        for field in ('id', 'level', 'energy'):
            row = event()
            del row['challenge_data'][field]
            variants.append({'time': 1000, 'active_events': [row]})
        for source in variants:
            with self.subTest(keys=list(source)):
                self.assertIsNone(resolve_pvp_challenge(source))

    def test_current_wrapped_state_deduplicates_and_does_not_mutate_source(self):
        row = event(energy=0)
        source = {'time': 1000, 'active_events': [row], 'account': {'active_events': {'41': copy.deepcopy(row)}}}
        before = copy.deepcopy(source)
        result = resolve_pvp_challenge(source)
        self.assertEqual(result['current'], 0)
        self.assertEqual(source, before)
        self.assertIsNone(resolve_pvp_challenge(source, '99'))


if __name__ == '__main__':
    unittest.main()
