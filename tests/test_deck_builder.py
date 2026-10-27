from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from atcommander.deck_builder import owned_cards, suggest_deck, deck_stamp, rename_deck, save_draft
from atcommander.local_commands import CommandInputError, CommandUnavailable
from test_deck_swap_safety import Service


def fixture():
    catalog = {'1': {'id': '1', 'name': 'Amy', 'card_type': 'character', 'rarity': 4, 'attack': 10, 'health': 30},
        '2': {'id': '2', 'name': 'Item', 'card_type': 'object', 'rarity': 4, 'attack': 12, 'health': 25, 'trait': ['athletic']},
        '3': {'id': '3', 'name': 'Combo', 'card_type': 'combo', 'rarity': 4, 'attack': 20, 'health': 50, 'trait': ['athletic']},
        '4': {'id': '4', 'name': 'PC', 'card_type': 'final_form', 'rarity': 4, 'attack': 25, 'health': 50, 'trait': ['athletic']}}
    units = {str(i): {'unit_index': i, 'unit_id': '1' if i <= 30 else '2' if i <= 60 else '4', 'level': 1} for i in range(1, 66)}
    return {'user_units': units, 'user_combos_compressed': {'1~2': 1}}, catalog, {'1~2': '3|1'}


class DeckSuggestionTests(unittest.TestCase):
    def setUp(self): self.snapshot, self.catalog, self.recipes = fixture()
    def suggest(self, **kwargs): return suggest_deck(self.snapshot, self.catalog, self.recipes, **kwargs)
    def test_balanced_owned_unique_twenty_five_with_full_researched_coverage(self):
        answer = self.suggest(); cards = answer['cards']
        self.assertEqual(len(cards), 25); self.assertEqual(len({r['unit_index'] for r in cards}), 25)
        self.assertEqual(sum(r['kind'] == 'character' for r in cards), 13)
        self.assertEqual(answer['coverage'], 100)
    def test_single_character_focus_does_not_widen_the_rule(self):
        answer = self.suggest(rule='One character + items', character='Amy')
        self.assertTrue(all(r['name'] == 'Amy' for r in answer['cards'] if r['kind'] == 'character'))
        with self.assertRaises(CommandUnavailable): self.suggest(rule='One character + items', character='Bender')
    def test_bge_filters_combo_result_not_only_item_traits(self):
        self.catalog['2']['trait'] = ['armed']
        self.assertEqual(self.suggest(rule='BGE / trait combos', trait='athletic')['coverage'], 100)
        with self.assertRaises(CommandUnavailable): self.suggest(rule='BGE / trait combos', trait='armed')
    def test_characters_only_uses_exact_copies(self):
        answer = self.suggest(rule='Characters only', character='Amy')
        self.assertEqual(len(answer['cards']), 25); self.assertTrue(all(r['kind'] == 'character' for r in answer['cards']))
    def test_unknown_research_state_blocks_default_combo_suggestion(self):
        del self.snapshot['user_combos_compressed']
        with self.assertRaises(CommandUnavailable): self.suggest()
        self.assertEqual(len(self.suggest(researched_only=False)['cards']), 25)
    def test_unresearched_pair_is_not_silently_used(self):
        self.snapshot['user_combos_compressed'] = {}
        with self.assertRaises(CommandUnavailable): self.suggest()
    def test_power_cards_match_trait_and_have_unique_instances(self):
        answer = self.suggest(rule='BGE / trait combos', trait='athletic', power_cards=5)
        self.assertEqual(sum(r['kind'] == 'final_form' for r in answer['cards']), 5)
        with self.assertRaises(CommandUnavailable): self.suggest(power_cards=6)
    def test_missing_character_or_trait_and_invalid_sizes_are_rejected(self):
        for options in [{'rule': 'One character + items'}, {'rule': 'BGE / trait combos'}, {'size': 24}, {'size': 36}, {'size': True}, {'size': '25'}, {'power_cards': '3'}, {'min_rarity': True}, {'rule': 'Characters only', 'power_cards': 1}]:
            with self.subTest(options=options), self.assertRaises(CommandInputError): self.suggest(**options)
    def test_not_enough_owned_copies_cannot_create_duplicates(self):
        self.snapshot['user_units'] = {k: v for k, v in self.snapshot['user_units'].items() if int(k) <= 35}
        with self.assertRaises(CommandUnavailable): self.suggest()
    def test_current_owned_levels_are_used_and_unknown_levels_are_skipped(self):
        self.catalog['1']['upgrade'] = [{'level': 2, 'attack': 30}]
        self.snapshot['user_units']['1']['level'] = 2
        pool = owned_cards(self.snapshot, self.catalog)
        self.assertEqual(next(r for r in pool if r['unit_index'] == 1)['attack'], 30)
        self.snapshot['user_units']['1']['level'] = 50
        self.assertNotIn(1, {r['unit_index'] for r in owned_cards(self.snapshot, self.catalog)})
    def test_stop_interrupts_large_pool_without_mutation(self):
        with self.assertRaises(CommandUnavailable): self.suggest(should_stop=lambda: True)


class DeckWriteTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.service = Service(Path(self.temp.name) / 'state.json')
        self.stamp = deck_stamp(self.service.live, 2)
    def rename(self, name='My renamed deck'): return rename_deck(self.service, 2, name, self.stamp)
    def install_rename(self, timeout=False, unchanged=False):
        def call(message, args):
            self.service.calls.append((message, deepcopy(args)))
            if not unchanged: self.service.live['user_decks']['2']['name'] = args['name']
            if timeout: raise TimeoutError('Synthetic lost reply')
            return {'result': True}
        self.service.call = call
    def test_rename_verified_and_keeps_cards_hero_and_active_deck(self):
        self.install_rename(); before = deepcopy(self.service.live)
        result = self.rename(); self.assertIn('renamed', result['message'])
        self.assertEqual(self.service.live['user_data'], before['user_data'])
        self.assertEqual(self.service.live['user_decks']['2']['units'], before['user_decks']['2']['units'])
        self.assertEqual(len(self.service.calls), 1)
        self.assertIn('Before rename slot 2', self.service.store.read()['decks'])
    def test_lost_rename_reply_reconciles_without_repeating(self):
        self.install_rename(timeout=True); self.rename(); self.assertEqual(len(self.service.calls), 1)
    def test_unconfirmed_rename_stops_and_is_not_replayed(self):
        self.install_rename(unchanged=True)
        with self.assertRaises(CommandUnavailable): self.rename()
        self.assertEqual(len(self.service.calls), 1)
    def test_changed_slot_since_preview_blocks_rename(self):
        self.install_rename(); self.service.live['user_decks']['2']['name'] = 'Changed elsewhere'
        with self.assertRaises(CommandUnavailable): self.rename()
        self.assertEqual(self.service.calls, [])
    def test_invalid_names_and_stop_send_no_write(self):
        for name in ['', ' ', 'bad\nname', 'x' * 41]:
            with self.assertRaises(CommandInputError): self.rename(name)
        self.service.stop.set()
        with self.assertRaises(CommandUnavailable): self.rename()
        self.assertEqual(self.service.calls, [])
    def test_save_draft_uses_existing_verified_single_write_with_backup(self):
        cards = [{'unit_index': i} for i in range(1, 26)]
        result = save_draft(self.service, 2, cards, self.stamp)
        self.assertIn('verified', result['message']); self.assertEqual(len(self.service.calls), 1)
        self.assertEqual(self.service.live['user_data']['active_deck'], 1)
        self.assertEqual(self.service.live['user_decks']['2']['commander']['unit_id'], '4001')
        self.assertIn('Before builder slot 2', self.service.store.read()['decks'])
    def test_stale_preview_missing_or_duplicate_cards_block_save(self):
        for cards in [[{'unit_index': 1}] * 25, [{'unit_index': i} for i in range(100, 125)], [{'unit_index': i} for i in range(1, 20)]]:
            with self.assertRaises((CommandUnavailable, CommandInputError)): save_draft(self.service, 2, cards, self.stamp)
        self.assertEqual(self.service.calls, [])
    def test_no_op_save_and_rename_send_no_request(self):
        self.rename('Slot 2')
        save_draft(self.service, 2, [{'unit_index': i} for i in range(26, 51)], self.stamp)
        self.assertEqual(self.service.calls, [])
    def test_unexpected_name_change_after_card_write_is_reported(self):
        def apply(args):
            self.service.apply(args); self.service.live['user_decks']['2']['name'] = 'Unexpected'
            return {'result': True}
        self.service.behavior = apply
        with self.assertRaises(CommandUnavailable): save_draft(self.service, 2, [{'unit_index': i} for i in range(1, 26)], self.stamp)
        self.assertEqual(len(self.service.calls), 1)


if __name__ == '__main__': unittest.main()
