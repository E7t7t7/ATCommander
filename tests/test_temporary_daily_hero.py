from copy import deepcopy
import unittest

from atcommander.local_commands import CommandUnavailable
from atcommander.temporary_daily_hero import exact_active_commander, temporary_daily_commander


def state(hero=1001, deck=1, units=(10, 11)):
    return {'result': True, 'user_data': {'active_deck': deck},
            'user_decks': {str(deck): {'deck_id': deck, 'commander': {'unit_id': hero},
                                      'units': [{'unit_index': i} for i in units]}}}


class FakeTransport:
    def __init__(self):
        self.current = state()
        self.calls = []
        self.reads = 0
        self.behaviors = []

    def read(self):
        self.reads += 1
        return deepcopy(self.current)

    def call(self, name, args):
        self.calls.append((name, deepcopy(args)))
        behavior = self.behaviors.pop(0) if self.behaviors else 'confirmed'
        if behavior != 'reject':
            self.current = state(int(args['commander_id']), int(args['deck_id']))
        if behavior == 'timeout':
            raise RuntimeError('synthetic-private-network-value')
        if behavior in {'partial', 'reject'}:
            return {'result': behavior != 'reject'}
        return self.read()


class TemporaryDailyHeroTests(unittest.TestCase):
    def setUp(self):
        self.transport = FakeTransport()

    def test_switch_and_restore_verified_around_play(self):
        with temporary_daily_commander(self.transport.call, self.transport.read, 4001, expected_deck_id=1) as report:
            self.assertEqual(exact_active_commander(self.transport.current).commander_id, '4001')
        self.assertEqual([args['commander_id'] for _, args in self.transport.calls], ['4001', '1001'])
        self.assertTrue(report['restored'])
        self.assertEqual(exact_active_commander(self.transport.current).commander_id, '1001')

    def test_restore_runs_when_play_raises(self):
        with self.assertRaisesRegex(RuntimeError, 'synthetic battle failure'):
            with temporary_daily_commander(self.transport.call, self.transport.read, 4001) as report:
                raise RuntimeError('synthetic battle failure')
        self.assertTrue(report['restored'])
        self.assertEqual(exact_active_commander(self.transport.current).commander_id, '1001')

    def test_current_matching_hero_does_not_assign_or_restore(self):
        with temporary_daily_commander(self.transport.call, self.transport.read, 1001) as report:
            pass
        self.assertEqual(self.transport.calls, [])
        self.assertEqual(report['restoration_status'], 'unchanged')

    def test_timeout_assignment_reconciles_without_repeating(self):
        self.transport.behaviors = ['timeout', 'confirmed']
        with temporary_daily_commander(self.transport.call, self.transport.read, 4001) as report:
            self.assertTrue(report['switch_verified'])
        self.assertTrue(report['restored'])
        self.assertEqual(len(self.transport.calls), 2)

    def test_rejected_assignment_never_runs_play_or_retries(self):
        self.transport.behaviors = ['reject']
        with self.assertRaises(CommandUnavailable):
            with temporary_daily_commander(self.transport.call, self.transport.read, 4001):
                self.fail('No play should begin after a rejected assignment.')
        self.assertEqual(len(self.transport.calls), 1)

    def test_partial_responses_reconcile_both_assignments(self):
        self.transport.behaviors = ['partial', 'partial']
        with temporary_daily_commander(self.transport.call, self.transport.read, 4001) as report:
            pass
        self.assertEqual(report['restoration_status'], 'restored_after_reconciliation')
        self.assertEqual(len(self.transport.calls), 2)

    def test_external_commander_change_is_preserved(self):
        with self.assertRaises(CommandUnavailable):
            with temporary_daily_commander(self.transport.call, self.transport.read, 4001) as report:
                self.transport.current = state(4002)
        self.assertEqual(report['restoration_status'], 'external_change')
        self.assertEqual(len(self.transport.calls), 1)
        self.assertEqual(exact_active_commander(self.transport.current).commander_id, '4002')

    def test_external_active_deck_or_card_change_is_preserved(self):
        for changed in (state(4001, deck=2), state(4001, units=(10, 12))):
            with self.subTest(changed=changed):
                transport = FakeTransport()
                with self.assertRaises(CommandUnavailable):
                    with temporary_daily_commander(transport.call, transport.read, 4001):
                        transport.current = changed
                self.assertEqual(len(transport.calls), 1)
                self.assertEqual(transport.current, changed)

    def test_external_restoration_needs_no_second_request(self):
        with temporary_daily_commander(self.transport.call, self.transport.read, 4001) as report:
            self.transport.current = state(1001)
        self.assertEqual(report['restoration_status'], 'already_original')
        self.assertEqual(len(self.transport.calls), 1)

    def test_unreadable_state_after_play_cannot_restore_blindly(self):
        with self.assertRaises(CommandUnavailable) as error:
            with temporary_daily_commander(self.transport.call, self.transport.read, 4001) as report:
                self.transport.current = {'result': False}
        self.assertEqual(report['restoration_status'], 'ambiguous')
        self.assertEqual(len(self.transport.calls), 1)
        self.assertNotIn('synthetic-private-network-value', str(error.exception))

    def test_ambiguous_restore_is_reconciled_once_never_replayed(self):
        self.transport.behaviors = ['confirmed', 'timeout']
        with temporary_daily_commander(self.transport.call, self.transport.read, 4001) as report:
            pass
        self.assertEqual(report['restoration_status'], 'restored_after_reconciliation')
        self.assertEqual(len(self.transport.calls), 2)

    def test_unconfirmed_restore_stops_without_retry(self):
        self.transport.behaviors = ['confirmed', 'reject']
        with self.assertRaises(CommandUnavailable):
            with temporary_daily_commander(self.transport.call, self.transport.read, 4001) as report:
                pass
        self.assertEqual(report['restoration_status'], 'unconfirmed')
        self.assertEqual(len(self.transport.calls), 2)

    def test_changed_planned_deck_stops_before_assignment(self):
        with self.assertRaises(CommandUnavailable):
            with temporary_daily_commander(self.transport.call, self.transport.read, 4001, expected_deck_id=2):
                self.fail('No play should begin after the active deck changed.')
        self.assertEqual(self.transport.calls, [])

    def test_changed_planned_commander_stops_before_assignment(self):
        with self.assertRaises(CommandUnavailable):
            with temporary_daily_commander(self.transport.call, self.transport.read, 4001, expected_commander_id=1002):
                self.fail('No play should begin after the commander changed.')
        self.assertEqual(self.transport.calls, [])

    def test_ambiguous_exact_deck_schema_cannot_select(self):
        for payload in ({}, {'result': False}, {'user_data': {'active_deck': 1}, 'user_decks': {}},
                        {'user_data': {'active_deck': 1}, 'user_decks': {'1': {'deck_id': 2, 'commander': {'unit_id': 1001}}}},
                        {'user_data': {'active_deck': 1}, 'user_decks': {'1': {'commander_id': 1001, 'commander': {'unit_id': 4001}}}}):
            with self.subTest(payload=payload), self.assertRaises(CommandUnavailable):
                exact_active_commander(payload)


if __name__ == '__main__':
    unittest.main()
