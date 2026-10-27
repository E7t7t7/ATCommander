from __future__ import annotations

import threading
import time
from typing import Any, Callable

from .api import ATClient, ATApiError
from .credentials import load_credentials
from .session_stats import SessionTracker, build_inventory_snapshot
from .persistent_stats import LifetimeTracker
from .strategy import SingleComboRushPolicy
from .account_state import find_arena_energy
from .preflight import ad_preflight


class ArenaRunner:
    def __init__(
        self,
        battle_limit: int = 1,
        move_delay: float = 0.10,
        between_battles_delay: float = 1.5,
        selected_deck_id: str | None = None,
        selected_hero_id: str | None = None,
        reserve_energy: int = 0,
        continuous_monitor: bool = False,
        energy_poll_seconds: int = 60,
        auto_watch_ads: bool = False,
        event: Callable[[dict[str, Any]], None] | None = None,
        *,
        bypass_turn_limit: bool = False,
    ):
        self.battle_limit = max(0, int(battle_limit))
        self.move_delay = max(0.05, float(move_delay))
        self.between_battles_delay = max(0.1, float(between_battles_delay))
        self.selected_deck_id = selected_deck_id or None
        self.selected_hero_id = selected_hero_id or None
        self.reserve_energy = max(0, int(reserve_energy))
        self.continuous_monitor = bool(continuous_monitor)
        self.energy_poll_seconds = max(15, int(energy_poll_seconds))
        self.auto_watch_ads = bool(auto_watch_ads)
        self.bypass_turn_limit = bool(bypass_turn_limit)
        self.event = event or (lambda _e: None)
        self.policy = SingleComboRushPolicy()
        self.tracker = SessionTracker()
        self.lifetime = LifetimeTracker()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._pause = threading.Event()
        self._client: ATClient | None = None
        self._card_catalog: dict[str, dict[str, Any]] = {}
        self._item_catalog: dict[str, dict[str, Any]] = {}

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._pause.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def pause(self, paused: bool = True) -> None:
        if paused:
            self._pause.set()
        else:
            self._pause.clear()
        self._emit("paused", paused=paused)

    def stop(self) -> None:
        self._stop.set()
        self._pause.clear()

    def refresh_snapshot(self) -> None:
        def work():
            try:
                if self._client is None:
                    self._client = ATClient(load_credentials())
                snap = self._capture_snapshot(self._client)
                if self.tracker.start_snapshot is None:
                    self.tracker.start(snap)
                else:
                    self.tracker.update_snapshot(snap)
                self.lifetime.ensure_baseline(snap)
                self._emit("snapshot", tracker=self.tracker.to_dict(), lifetime=self.lifetime.to_dict())
            except Exception as exc:
                self._emit("error", message=f"Snapshot refresh failed: {exc}")
        threading.Thread(target=work, daemon=True).start()

    def _wait_if_paused(self) -> bool:
        while self._pause.is_set() and not self._stop.is_set():
            time.sleep(0.1)
        return not self._stop.is_set()

    @staticmethod
    def _explicit_false(value: Any) -> bool:
        if isinstance(value, bool):
            return value is False
        if isinstance(value, (int, float)):
            return value <= 0
        if isinstance(value, str):
            return value.strip().lower() in {"0", "false", "off", "no", "none", "inactive"}
        return False

    @staticmethod
    def _explicit_true(value: Any) -> bool:
        if isinstance(value, bool):
            return value is True
        if isinstance(value, (int, float)):
            return value > 0
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "on", "yes", "active", "complete", "completed", "max"}
        return False

    def _ad_preflight(self, init_data: dict[str, Any]) -> None:
        ad_preflight(self._client, init_data, lambda m:self._emit("log", message=m), auto_watch=self.auto_watch_ads)

    def _apply_requested_selection(self) -> None:
        if not (self.selected_deck_id or self.selected_hero_id):
            return
        self._client.apply_selection(deck_id=self.selected_deck_id, hero_id=self.selected_hero_id)
        self._emit("log", message="Start-time deck/hero selection applied.")

    def _run(self) -> None:
        try:
            creds = load_credentials()
            if not creds.ready:
                raise ATApiError("AT login credentials are unavailable. Open Diagnostics / Learn → Account and save or refresh the credentials there.")
            self._client = ATClient(creds, timeout=25)
            self._emit("status", text="Connecting / taking starting inventory snapshot…")
            init_data = self._client.init()
            # Only the legitimate AT browser ad UI may be automated. The final
            # gate is always the captured getBoostLevel read; no ad-completion
            # endpoint is ever replayed directly.
            self._ad_preflight(init_data)
            self._apply_requested_selection()
            start_snap = self._capture_snapshot(self._client, init_data=init_data)
            self.tracker.start(start_snap)
            self.lifetime.ensure_baseline(start_snap)
            self._emit("session_start", tracker=self.tracker.to_dict(), lifetime=self.lifetime.to_dict())
            self._emit("status", text="Running")

            completed = 0
            while not self._stop.is_set():
                if self.battle_limit and completed >= self.battle_limit:
                    break
                if not self._wait_if_paused():
                    break

                # Optional reserve-energy guard. If AT exposes a usable arena-energy field,
                # stop at the reserve or keep monitoring until regeneration pushes above it.
                try:
                    cur_init = self._client.init()
                    energy, energy_path = find_arena_energy(cur_init)
                    if energy is not None:
                        self._emit("energy", current=energy, reserve=self.reserve_energy, path=energy_path)
                        if energy <= self.reserve_energy:
                            if self.continuous_monitor:
                                self._emit("log", message=f"Arena energy {energy} reached reserve {self.reserve_energy}; monitoring every {self.energy_poll_seconds}s.")
                                self._stop.wait(self.energy_poll_seconds)
                                continue
                            self._emit("log", message=f"Arena reserve reached ({energy}/{self.reserve_energy}); session complete.")
                            break
                except Exception as exc:
                    self._emit("log", message=f"Arena energy check unavailable; continuing normally: {exc}")

                battle_no = completed + 1
                try:
                    response = self._client.start_arena_battle()
                except ATApiError as exc:
                    msg = str(exc)
                    lowered = msg.lower()
                    if any(w in lowered for w in ("energy", "hunting", "stamina", "not enough", "no energy")):
                        self._emit("log", message=f"Arena energy exhausted — session complete. ({msg})")
                        break
                    raise

                state = self._client.extract_battle_data(response)
                if not state or not state.get("battle_id"):
                    # In the long-run test AT returned a non-battle response only
                    # after all remaining energy had been consumed. Treat that as
                    # a clean end in unlimited mode and expose keys for debugging.
                    keys = ", ".join(sorted(map(str, response.keys()))) if isinstance(response, dict) else type(response).__name__
                    if self.battle_limit == 0:
                        self._emit("log", message=f"No new Arena battle was returned; treating this as end-of-energy/session. Response keys: {keys or 'none'}")
                        break
                    raise ATApiError(f"startHuntingBattle did not return usable battle_data. Response keys: {keys or 'none'}")

                opponent = self._client.opponent_name(state)
                self._emit("battle_start", number=battle_no, opponent=opponent, battle_id=state.get("battle_id"))
                won, moves, _final_response, _final_state = self._drive_one(state, battle_no, opponent)
                if self._stop.is_set():
                    break

                completed += 1
                self.tracker.record_battle(opponent, won, moves)
                self.lifetime.record_battle("Arena", won)
                self._emit(
                    "battle_end",
                    number=completed,
                    opponent=opponent,
                    won=won,
                    moves=moves,
                    tracker=self.tracker.to_dict(),
                    lifetime=self.lifetime.to_dict(),
                )

                try:
                    snap = self._capture_snapshot(self._client, catalogs=False)
                    self.tracker.update_snapshot(snap)
                    self.lifetime.update_snapshot(snap)
                    self._emit("snapshot", tracker=self.tracker.to_dict(), lifetime=self.lifetime.to_dict())
                except Exception as exc:
                    self._emit("log", message=f"Inventory refresh skipped: {exc}")

                if not self._wait_if_paused():
                    break
                time.sleep(self.between_battles_delay)

            try:
                snap = self._capture_snapshot(self._client, catalogs=False)
                self.tracker.update_snapshot(snap)
                self.lifetime.update_snapshot(snap)
            except Exception:
                pass
            self.tracker.finish()
            self._emit("finished", tracker=self.tracker.to_dict(), lifetime=self.lifetime.to_dict(), stopped=self._stop.is_set())
        except Exception as exc:
            try:
                self.tracker.finish()
            except Exception:
                pass
            self._emit("fatal", message=str(exc), tracker=self.tracker.to_dict(), lifetime=self.lifetime.to_dict())

    def _drive_one(self, state: dict[str, Any], battle_no: int, opponent: str):
        battle_id = state.get("battle_id")
        host_id = state.get("host_id")
        current = state
        last_response: dict[str, Any] = {}

        move_no = 0
        while self.bypass_turn_limit or move_no < 30:
            move_no += 1
            if not self._wait_if_paused():
                return None, move_no - 1, last_response, current

            decision = self.policy.choose(current)
            if decision is None:
                raise ATApiError("No legal play could be derived from the returned battle state.")

            self._emit(
                "move",
                battle=battle_no,
                move=move_no,
                opponent=opponent,
                action=decision.action,
                card_uid=decision.card_uid,
                card_uid2=decision.card_uid2,
                reason=decision.reason,
                score=decision.score,
            )

            response = self._client.play_battlefield_card(
                battle_id=battle_id,
                host_id=host_id,
                card_uid=decision.card_uid,
                card_uid2=decision.card_uid2,
            )
            last_response = response
            next_state = self._client.extract_battle_data(response)
            if self._client.battle_finished(response, next_state):
                won = self._client.extract_outcome(response, next_state)
                return won, move_no, response, next_state

            if not next_state or not next_state.get("battle_id"):
                raise ATApiError("The card play did not return a continuing battle or confirmed result. It was not retried.")
            current = next_state
            battle_id = current.get("battle_id", battle_id)
            host_id = current.get("host_id", host_id)
            time.sleep(self.move_delay)

        raise ATApiError("Safety stop: battle exceeded 30 moves.")

    def _capture_snapshot(self, client: ATClient, catalogs: bool = True, init_data: dict[str, Any] | None = None) -> dict[str, Any]:
        init_data = init_data if isinstance(init_data, dict) else client.init()
        if catalogs and not self._card_catalog:
            try:
                self._card_catalog = client.card_catalog()
            except Exception as exc:
                self._emit("log", message=f"Card-name catalog unavailable; IDs will be shown: {exc}")
        if catalogs and not self._item_catalog:
            try:
                self._item_catalog = client.item_catalog()
            except Exception as exc:
                self._emit("log", message=f"Item-name catalog unavailable; IDs will be shown: {exc}")
        return build_inventory_snapshot(init_data, self._card_catalog, self._item_catalog)

    def _emit(self, kind: str, **payload: Any) -> None:
        try:
            self.event({"kind": kind, **payload})
        except Exception:
            pass
