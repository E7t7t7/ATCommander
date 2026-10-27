from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from atcommander import ad_ui, research_ad_ui
from atcommander import research_unlock_ui as unlock
from tests.test_research_ad_unlock_targets import snapshot, started, context


def selection(**changes):
    return {'ok': True, 'verified_public_build': True, 'chamber': 3,
            'ad_locked': True, 'selected_card_ids': [22, 23], **changes}


POINTS = {name: {'x': .3, 'y': .6, 'id': name}
          for name in ('research', 'chamber', 'inventory', 'watch', 'close')}


class ResearchUnlockUITests(unittest.TestCase):
    def flow(self, *, select=None, inspect=None, fresh=None, after=None, input_error=None,
             stop=None, watch_missing=False, initial=None, on_read=None, on_inspect=None,
             marker_after_inventory=True, marker_after_selection=True,
             initial_native=None, reused=False, on_prepare=None):
        from atcommander import native_research_ui
        clock, inputs, reads, inspections, events = [0.0], [], [], [], []
        prep = {'ok': True}
        def init():
            events.append('snapshot')
            reads.append(clock[0])
            state = (initial if len(reads) == 1 else fresh if len(reads) == 2 else after)
            if state is None:
                state = snapshot() if len(reads) < 3 else started()
            if on_read is not None:
                on_read(len(reads))
            return deepcopy(state)
        client = SimpleNamespace(init=Mock(side_effect=init), prepared=prep, events=events)
        def prepare(**kwargs):
            events.append('prepare')
            self.assertIs(kwargs['client'], client)
            self.assertTrue(callable(kwargs['should_stop']))
            if on_prepare is not None:
                on_prepare()
            return prep
        def view(_prep):
            canvas = {'x': 0, 'y': 0, 'width': 600, 'height': 400}
            seen = {point['id'] for _, point in inputs}
            if 'research' not in seen and not reused:
                return {'research_entry': POINTS['research']}, canvas
            if 'chamber' not in seen and not reused:
                return {'research_hub_marker': {}, 'research_ad_chamber': POINTS['chamber']}, canvas
            if 'watch' not in seen:
                marker = (not reused and ('inventory' not in seen or marker_after_inventory)
                          and (not picker.called or marker_after_selection))
                controls = {'research_picker_marker': POINTS['inventory']} if marker else {}
                if not watch_missing:
                    controls['research_unlock_watch_button'] = POINTS['watch']
                controls['paid_button'] = {'id': 'paid'}
                return controls, canvas
            return {'close': POINTS['close'], 'destination': {'id': 'advertiser'}}, canvas
        def click(_prep, _canvas, point):
            self.assertTrue(callable(_prep.get('_commander_should_stop')))
            inputs.append((clock[0], point))
            if input_error == point['id']:
                raise RuntimeError('synthetic-private-detail')
            if input_error == 'stopped-' + point['id']:
                from atcommander.ad_browser_focus import GameInputStopped
                raise GameInputStopped()
        def read_selection(_prep):
            self.assertTrue(callable(_prep.get('_commander_should_stop')))
            inspections.append(clock[0])
            if len(inspections) == 1:
                return deepcopy(initial_native) if initial_native is not None else {'ok': False, 'reason': 'popup_unavailable'}
            if on_inspect is not None:
                on_inspect()
            return inspect or selection()
        with patch.object(ad_ui, 'prepare_ad_game_ui', side_effect=prepare), \
             patch.object(research_ad_ui, '_view', side_effect=view), \
             patch.object(research_ad_ui, '_click', side_effect=click), \
             patch.object(native_research_ui, 'select_native_research_pair', return_value=select or selection()) as picker, \
             patch.object(native_research_ui, 'read_native_research_selection', side_effect=read_selection), \
             patch.object(unlock.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(unlock.time, 'sleep', side_effect=lambda seconds: clock.__setitem__(0, clock[0] + seconds)):
            result = unlock.play_research_unlock_ad(
                client, context(), wait_seconds=60,
                should_stop=(lambda: stop(inputs)) if stop else None)
        return result, inputs, picker, client

    def test_home_to_exact_pair_to_real_watch_and_own_close(self):
        result, inputs, picker, client = self.flow()
        self.assertTrue(result['ok'])
        self.assertEqual(result['pair'], '22~23')
        self.assertEqual(result['gems_spent'], 0)
        self.assertEqual([point['id'] for _, point in inputs],
                         ['research', 'chamber', 'inventory', 'watch', 'close'])
        self.assertEqual(picker.call_count, 1)
        self.assertEqual(picker.call_args.args[1], [22, 23])
        self.assertEqual(client.init.call_count, 3)
        watched = next(stamp for stamp, point in inputs if point['id'] == 'watch')
        closed = next(stamp for stamp, point in inputs if point['id'] == 'close')
        self.assertGreaterEqual(closed - watched, 13)

    def test_wrong_native_card_or_build_never_reaches_watch(self):
        for state in (selection(selected_card_ids=[22, 24]), selection(chamber=2),
                      selection(verified_public_build=False), selection(ad_locked=False)):
            with self.subTest(state=state):
                result, inputs, *_ = self.flow(select=state)
                self.assertFalse(result['ok'])
                self.assertNotIn('watch', [point['id'] for _, point in inputs])

    def test_inventory_label_may_disappear_after_its_single_input_or_selection(self):
        for options in ({'marker_after_inventory': False}, {'marker_after_selection': False}):
            with self.subTest(options=options):
                result, inputs, picker, client = self.flow(**options)
                self.assertTrue(result['ok'])
                self.assertEqual([point['id'] for _, point in inputs],
                                 ['research', 'chamber', 'inventory', 'watch', 'close'])
                picker.assert_called_once()
                self.assertEqual(client.init.call_count, 3)

    def test_disappearing_inventory_label_cannot_substitute_for_exact_selection(self):
        result, inputs, picker, _client = self.flow(
            marker_after_inventory=False, select=selection(selected_card_ids=[22, 24]))
        self.assertFalse(result['ok'])
        self.assertFalse(result['playback_started'])
        self.assertEqual([point['id'] for _, point in inputs], ['research', 'chamber', 'inventory'])
        picker.assert_called_once()

    def test_card_change_after_selection_stops_immediately_before_watch(self):
        result, inputs, *_ = self.flow(inspect=selection(selected_card_ids=[24, 23]))
        self.assertFalse(result['ok'])
        self.assertNotIn('watch', [point['id'] for _, point in inputs])

    def test_changed_gem_or_ownership_stops_before_watch(self):
        for change in ('gem', 'owned'):
            state = snapshot()
            if change == 'gem':
                state['user_data']['free_hard_currency'] = 499
            else:
                del state['user_units']['6']
            result, inputs, *_ = self.flow(fresh=state)
            self.assertFalse(result['ok'])
            self.assertNotIn('watch', [point['id'] for _, point in inputs])

    def test_unknown_watch_input_is_never_replayed_or_reported_as_credit(self):
        result, inputs, _, client = self.flow(input_error='watch')
        self.assertFalse(result['ok'])
        self.assertTrue(result['playback_started'])
        self.assertEqual([point['id'] for _, point in inputs].count('watch'), 1)
        self.assertEqual(client.init.call_count, 2)
        self.assertNotIn('synthetic-private-detail', str(result))

    def test_missing_specific_watch_artwork_never_clicks_paid_control(self):
        result, inputs, *_ = self.flow(watch_missing=True)
        self.assertFalse(result['ok'])
        self.assertEqual([point['id'] for _, point in inputs], ['research', 'chamber', 'inventory'])

    def test_changed_exact_task_or_spent_gems_do_not_confirm_unlock(self):
        for change in ('pair', 'gem'):
            state = started()
            if change == 'pair':
                state['user_tasks']['201']['task_id_2'] = 24
            else:
                state['user_data']['free_hard_currency'] -= 1
            result, inputs, *_ = self.flow(after=state)
            self.assertFalse(result['ok'])
            self.assertEqual([point['id'] for _, point in inputs].count('watch'), 1)
            self.assertEqual([point['id'] for _, point in inputs].count('close'), 1)

    def test_stop_after_inventory_prevents_selection_and_watch(self):
        result, inputs, picker, client = self.flow(
            stop=lambda inputs: any(point['id'] == 'inventory' for _, point in inputs))
        self.assertFalse(result['ok'])
        picker.assert_not_called()
        self.assertEqual(client.init.call_count, 1)

    def test_stop_during_final_fresh_account_read_never_delivers_watch(self):
        stopped = [False]
        def account_read(count):
            if count == 2:
                stopped[0] = True
        result, inputs, picker, client = self.flow(
            stop=lambda _inputs: stopped[0], on_read=account_read)
        self.assertFalse(result['ok'])
        self.assertFalse(result['playback_started'])
        self.assertNotIn('watch', [point['id'] for _, point in inputs])
        self.assertEqual(client.init.call_count, 2)
        picker.assert_called_once()

    def test_stop_during_final_native_inspection_never_delivers_watch(self):
        stopped = [False]
        result, inputs, picker, client = self.flow(
            stop=lambda _inputs: stopped[0],
            on_inspect=lambda: stopped.__setitem__(0, True))
        self.assertFalse(result['ok'])
        self.assertFalse(result['playback_started'])
        self.assertNotIn('watch', [point['id'] for _, point in inputs])
        self.assertEqual(client.init.call_count, 2)
        picker.assert_called_once()

    def test_unknown_pair_after_startup_never_reads_controls_or_starts_ad(self):
        state = snapshot()
        state['user_combos_compressed'] = {'22~23': '90|'}
        with patch.object(ad_ui, 'prepare_ad_game_ui', return_value={'ok': True}) as prepare, \
             patch.object(research_ad_ui, '_view') as view:
            result = unlock.play_research_unlock_ad(SimpleNamespace(init=lambda: state), context())
        self.assertFalse(result['ok'])
        prepare.assert_called_once()
        view.assert_not_called()
