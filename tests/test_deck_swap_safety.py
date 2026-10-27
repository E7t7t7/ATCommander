from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
from types import SimpleNamespace
import unittest

from atcommander.command_store import CommandStore
from atcommander.deck_swap_safety import restore_saved_deck,swap_saved_decks
from atcommander.local_commands import CommandUnavailable


def deck(identity, first, hero):
    return {"deck_id":identity,"name":f"Slot {identity}","commander":{"unit_id":hero},
            "units":[{"unit_index":index} for index in range(first,first+25)]}


class Service:
    def __init__(self,path):
        self.live={"result":True,"user_data":{"active_deck":1},
            "user_decks":{"1":deck(1,1,1001),"2":deck(2,26,4001)},
            "user_units":{str(index):{"unit_index":index} for index in range(1,51)}}
        self.store=CommandStore(path);self.calls=[];self.stop=threading.Event();self.invalidations=0
        self.context=SimpleNamespace(invalidate=self.invalidate);self.behavior=None;self.after_snapshot=None
    def invalidate(self):self.invalidations+=1
    def snapshot(self,refresh=False):
        if self.after_snapshot:self.after_snapshot()
        return deepcopy(self.live)
    def apply(self,args):
        row=self.live["user_decks"][str(args["deck_id"])]
        row["commander"]={"unit_id":args["commander_index"]}
        row["units"]=[{"unit_index":index} for index in json.loads(args["units"])]
    def call(self,name,args):
        self.calls.append((name,deepcopy(args)))
        if self.behavior:return self.behavior(args)
        self.apply(args);return {"result":True}


class DeckSwapSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.service=Service(Path(self.temp.name)/"state.json")
    def backups(self):return self.service.store.read().get("decks",{})

    def test_different_decks_and_heroes_swap_and_preserve_active_selection(self):
        result=swap_saved_decks(self.service,1,2)
        self.assertTrue(result["verified"]);self.assertEqual(len(self.service.calls),2)
        self.assertEqual(self.service.live["user_data"]["active_deck"],1)
        self.assertEqual(self.service.live["user_decks"]["1"]["commander"]["unit_id"],"4001")
        self.assertEqual(self.service.live["user_decks"]["2"]["units"][0]["unit_index"],1)
        self.assertEqual(self.backups()["Before swap slot 1"]["commander"]["unit_id"],"1001")
        self.assertGreaterEqual(self.service.invalidations,5)

    def test_invalid_second_direction_is_checked_before_any_write(self):
        self.service.live["user_decks"]["1"]["units"].pop()
        with self.assertRaises(CommandUnavailable):swap_saved_decks(self.service,1,2)
        self.assertEqual(self.service.calls,[])

    def test_missing_or_duplicate_cards_cannot_start_either_swap_direction(self):
        for corruption in ("missing","duplicate","boolean"):
            with self.subTest(corruption=corruption):
                service=Service(Path(self.temp.name)/(corruption+".json"))
                if corruption=="missing":service.live["user_units"].pop("1")
                elif corruption=="duplicate":service.live["user_decks"]["1"]["units"][1]={"unit_index":1}
                else:service.live["user_decks"]["1"]["units"][1]={"unit_index":True}
                with self.assertRaises(CommandUnavailable):swap_saved_decks(service,1,2)
                self.assertEqual(service.calls,[])

    def test_ambiguous_hero_metadata_cannot_start(self):
        self.service.live["user_decks"]["2"]["commander_id"]=1001
        with self.assertRaises(CommandUnavailable):swap_saved_decks(self.service,1,2)
        self.assertEqual(self.service.calls,[])

    def test_success_response_with_no_applied_state_stops_before_second_write(self):
        self.service.behavior=lambda args:{"result":True}
        with self.assertRaises(CommandUnavailable):swap_saved_decks(self.service,1,2)
        self.assertEqual(len(self.service.calls),1)
        self.assertEqual(len(self.backups()),2)

    def test_api_rejection_with_matching_deck_fields_still_fails(self):
        def reject(args):
            self.service.apply(args)
            return {"result":False,"user_decks":deepcopy(self.service.live["user_decks"])}
        self.service.behavior=reject
        with self.assertRaises(CommandUnavailable):swap_saved_decks(self.service,1,2)
        self.assertEqual(len(self.service.calls),1)

    def test_applied_timeout_reconciles_from_fresh_state_without_repeating(self):
        def timeout(args):
            self.service.apply(args)
            if len(self.service.calls)==1:raise TimeoutError("Synthetic unknown outcome")
            return {"result":True}
        self.service.behavior=timeout
        self.assertTrue(swap_saved_decks(self.service,1,2)["verified"])
        self.assertEqual(len(self.service.calls),2)

    def test_unapplied_timeout_stops_and_preserves_both_original_backups(self):
        self.service.behavior=lambda args:(_ for _ in ()).throw(TimeoutError("Synthetic unknown outcome"))
        with self.assertRaises(CommandUnavailable):swap_saved_decks(self.service,1,2)
        self.assertEqual(len(self.service.calls),1);self.assertEqual(len(self.backups()),2)

    def test_second_rejection_has_no_automatic_rollback(self):
        def reject_second(args):
            if len(self.service.calls)==2:return {"result":False}
            self.service.apply(args);return {"result":True}
        self.service.behavior=reject_second
        with self.assertRaises(CommandUnavailable):swap_saved_decks(self.service,1,2)
        self.assertEqual(len(self.service.calls),2)
        self.assertEqual(self.service.live["user_decks"]["1"]["units"][0]["unit_index"],26)
        self.assertEqual(self.backups()["Before swap slot 1"]["units"][0]["unit_index"],1)

    def test_external_second_slot_or_active_change_prevents_second_write(self):
        for change in ("slot","active"):
            with self.subTest(change=change):
                service=Service(Path(self.temp.name)/(change+".json"))
                def first(args):
                    service.apply(args)
                    if change=="slot":service.live["user_decks"]["2"]["commander"]={"unit_id":9999}
                    else:service.live["user_data"]["active_deck"]=2
                    return {"result":True}
                service.behavior=first
                with self.assertRaises(CommandUnavailable):swap_saved_decks(service,1,2)
                self.assertEqual(len(service.calls),1)

    def test_stop_after_first_write_prevents_second_and_keeps_backups(self):
        def first(args):
            self.service.apply(args);self.service.stop.set();return {"result":True}
        self.service.behavior=first
        with self.assertRaises(CommandUnavailable):swap_saved_decks(self.service,1,2)
        self.assertEqual(len(self.service.calls),1);self.assertEqual(len(self.backups()),2)

    def test_restore_uses_fresh_verification_and_minimal_local_backup(self):
        saved=deepcopy(self.service.live["user_decks"]["2"])
        self.service.live["user_decks"]["1"]["runtime_private_metadata"]="Synthetic local private field"
        answer=restore_saved_deck(self.service,1,saved)
        self.assertTrue(answer["verified"]);self.assertEqual(len(self.service.calls),1)
        self.assertNotIn("runtime_private_metadata",self.backups()["Before restore slot 1"])

    def test_empty_destination_can_receive_full_owned_backup(self):
        saved=deepcopy(self.service.live["user_decks"]["2"])
        self.service.live["user_decks"]["1"]["units"]=[]
        self.assertTrue(restore_saved_deck(self.service,1,saved)["verified"])


if __name__=="__main__":unittest.main()
