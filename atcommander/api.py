from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable
import time
import requests

from .credentials import Credentials
from .public_client_version import PublicClientVersionError, resolve_public_client_profile

API_URL = "https://cb-live.synapsegames.com/api.php"


class ATApiError(RuntimeError):
    pass


class ATNetworkError(ATApiError):
    """Sanitized transport classification; callers decide whether a read retries."""
    _CATEGORIES=frozenset({"ReadTimeout","ConnectTimeout","Timeout","ConnectionError",
                           "ProxyError","SSLError","HTTPError","InvalidURL","InvalidProxyURL",
                           "MissingSchema","InvalidSchema","URLRequired","InvalidHeader",
                           "TooManyRedirects","ChunkedEncodingError","ContentDecodingError",
                           "RetryError","RequestException"})

    def __init__(self,category="ReadTimeout",*,retryable_read=True):
        self.category=category if isinstance(category,str) and category in self._CATEGORIES else "RequestException"
        self.retryable_read=retryable_read is True
        super().__init__(f"Network/API request failed ({self.category}).")


def _network_error(error):
    """Inspect exception classes/status only; never include request text or URLs."""
    exceptions=requests.exceptions
    for name in ("SSLError","InvalidProxyURL","InvalidURL","MissingSchema","InvalidSchema",
                 "URLRequired","InvalidHeader","TooManyRedirects"):
        if isinstance(error,getattr(exceptions,name)):
            return ATNetworkError(name,retryable_read=False)
    if isinstance(error,exceptions.HTTPError):
        response=getattr(error,"response",None)
        status=getattr(response,"status_code",None) if response is not None else None
        return ATNetworkError("HTTPError",retryable_read=type(status) is int and status in {408,429,500,502,503,504})
    for name in ("ReadTimeout","ConnectTimeout","Timeout","ProxyError","ConnectionError"):
        if isinstance(error,getattr(exceptions,name)):
            return ATNetworkError(name,retryable_read=True)
    for name in ("ChunkedEncodingError","ContentDecodingError","RetryError"):
        if isinstance(error,getattr(exceptions,name)):
            return ATNetworkError(name,retryable_read=False)
    return ATNetworkError("RequestException",retryable_read=False)


@dataclass
class BattleSummary:
    mode: str
    opponent: str = "Unknown"
    winner: str = "Unknown"
    raw: dict[str, Any] | None = None

    @property
    def won(self) -> bool | None:
        w = str(self.winner).strip().lower()
        if w in {"1", "true", "player", "attacker", "you", "win", "won", "victory"}:
            return True
        if w in {"0", "false", "opponent", "defender", "loss", "lost", "defeat"}:
            return False
        return None


class ATClient:
    # New account-tool batches need gentler pacing than battle-card plays.
    # Established combat/purchase runners keep their existing timings.
    command_read_interval = 8.0

    def __init__(self, credentials: Credentials, timeout: int = 25, *, should_stop: Callable[[], bool] | None = None):
        if not credentials.ready:
            raise ATApiError("Animation Throwdown credentials are not available.")
        self.credentials = credentials
        self.timeout = timeout
        self._should_stop = should_stop
        self._init_snapshot: dict[str, Any] = {}
        # Challenge/Siege starts validate native forms; their moves also need
        # native signatures for the server to award rewards and progression.
        # Scope this to accepted starts on this client so established Arena and
        # Rumble requests retain their proven transport.
        self._signed_battle_ids: dict[str, dict[str, str]] = {}
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 ATCommander/1.7.26",
            "Accept": "application/json,text/plain,*/*",
        })

    def call(self, message: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._call(message, data, refresh_rejected_start=True)

    def _call(self, message: str, data: dict[str, Any] | None, *, refresh_rejected_start: bool,
              verified_profile: dict[str, str] | None = None,
              prior_private_literals: frozenset[str] = frozenset()) -> dict[str, Any]:
        payload: dict[str, Any] = {"password": self.credentials.password_hash,
                                   "client_time": str(int(time.time()))}
        if data:
            payload.update(data)
        signed_start = message in {"startChallenge", "fightGuildSiege"}
        signed_request = signed_start or (
            message in {"playBattlefieldCard", "playCard"}
            and str(payload.get("battle_id") or "") in self._signed_battle_ids
        )
        if signed_request:
            if self._should_stop and self._should_stop():
                raise ATApiError("Combat request stopped before sending.")
            from .combat_signing import sign_combat_fields
            try:
                profile = (verified_profile or resolve_public_client_profile()) if signed_start else self._signed_battle_ids[str(payload["battle_id"])]
            except PublicClientVersionError as exc:
                raise ATApiError(str(exc)) from None
            # These starts validate the current Unity client's signatures. A
            # learned request's timestamp/digests are never replayable.
            payload = sign_combat_fields(payload, message, user_id=self.credentials.user_id, **profile)
        if signed_request and self._should_stop and self._should_stop():
            raise ATApiError("Combat request stopped before sending.")
        try:
            r = self.session.post(
                API_URL,
                params={"message": message, "user_id": self.credentials.user_id},
                data=payload,
                timeout=self.timeout,
            )
            r.raise_for_status()
        except requests.RequestException as e:
            # requests exceptions may contain private URL query parameters.
            raise _network_error(e) from None
        if not r.content:
            return {}
        try:
            obj = r.json()
        except ValueError as e:
            # A successful final card play has been observed returning an empty
            # body. Treat genuinely empty responses as {}, but reject HTML/etc.
            if not r.text.strip():
                return {}
            raise ATApiError("AT returned a non-JSON response.") from None
        if signed_start and refresh_rejected_start and isinstance(obj, dict) and not obj.get("battle_data"):
            from .shop_rewards import api_rejected
            warning = str(obj.get("result_message") or obj.get("error") or "").casefold()
            marker = obj.get("result")
            explicitly_refused = (marker is False or type(marker) is int and marker == 0
                                  or isinstance(marker, str) and marker.casefold() in {"false", "0"})
            if explicitly_refused and api_rejected(obj) and "warning_157" in warning:
                # A refused start did not authorize combat. Refresh public build
                # metadata once and retry only when the verified profile changed.
                # Accepted/ambiguous starts and card moves are never replayed.
                try:
                    refreshed = resolve_public_client_profile(force_refresh=True)
                except PublicClientVersionError as exc:
                    raise ATApiError(str(exc)) from None
                if refreshed != profile:
                    from .diagnostic_privacy import private_literals
                    return self._call(message, data, refresh_rejected_start=False, verified_profile=refreshed,
                        prior_private_literals=prior_private_literals | private_literals({"payload": payload}))
        if isinstance(obj, dict) and obj.get("error"):
            err = obj.get("error")
            if isinstance(err, dict):
                err = err.get("message") or err.get("error") or str(err)
            from .diagnostic_privacy import private_literals, safe_text
            # Server errors can echo opaque payload values or private headers.
            # Capture known private strings only in memory, never in reports.
            headers = getattr(self.session, "headers", {})
            cookies = getattr(self.session, "cookies", {})
            private_context = {
                "user_id": self.credentials.user_id,
                "password": self.credentials.password_hash,
                "payload": payload,
                "credentials": dict(headers.items()) if hasattr(headers, "items") else {},
                "cookies": dict(cookies.items()) if hasattr(cookies, "items") else {},
            }
            raise ATApiError(safe_text(str(err), private_literals(private_context) | prior_private_literals)) from None
        if isinstance(obj, dict) and message in {"startChallenge", "fightGuildSiege"}:
            # The native CombatManager accepts the current top-level battle.
            # Cached/historical nested battle data must not authorize moves.
            from .shop_rewards import api_rejected
            battle = obj.get("battle_data")
            accepted = obj.get("result") is True or obj.get("result") == 1 or str(obj.get("result")).casefold() in {"true", "1"}
            if accepted and not api_rejected(obj) and isinstance(battle, dict) and battle.get("battle_id"):
                identity = str(battle["battle_id"])
                self._signed_battle_ids.pop(identity, None)
                self._signed_battle_ids[identity] = dict(profile)
                while len(self._signed_battle_ids) > 128:
                    self._signed_battle_ids.pop(next(iter(self._signed_battle_ids)))
        return obj if isinstance(obj, dict) else {"result": obj}

    def init(self) -> dict[str, Any]:
        response = self.call("init")
        if response.get("result") is not False and response.get("user_data"):
            self._init_snapshot = response
        return response

    def get_user_account(self) -> dict[str, Any]:
        """Read the compact live account/status payload used by the web client.

        The user's captured traffic confirms this response contains common_fields
        (including SFC rating) and user_tasks (including active Swole training).
        """
        return self.call("getUserAccount")

    def get_player_prefs_snapshot(self, mission_id: str | int) -> dict[str, Any]:
        """Return the full player_prefs_data using a confirmed Adventure call.

        The captured client writes auto_battle_mission_id via setPlayerPrefs and
        the response contains the full preference snapshot. Re-applying the stage
        the user selected for farming is an intentional no-op for our purposes.
        """
        import json as _json
        payload={"auto_battle_mission_id":{"value":str(mission_id),"type":"int"}}
        return self.call("setPlayerPrefs", {"player_prefs_data": _json.dumps(payload,separators=(",",":"))})

    def buy_store_item(self, item_id: str | int, expected_cost: str | int, quantity: str | int = 1) -> dict[str, Any]:
        """Buy a live store item using the selected store ID/cost.

        Kept separate from learned profiles so choosing a different Nixon pack can
        never replay the one literal product captured during learning.
        """
        # For the proven Nixon shop request, expected_cost is the selected
        # pack's unit price. 1.7.14 multiplied it by quantity without a capture
        # proving that shape; the live test produced a dead/failed bulk path.
        return self.call("buyStoreItem", {
            "cost_type": "2",
            "item_id": str(item_id),
            "quantity": str(quantity),
            "expected_cost": str(expected_cost),
        })

    def use_item(self, item_id: str | int, quantity: str | int = 1) -> dict[str, Any]:
        """Use a selected consumable item by its actual inventory ID."""
        return self.call("useItem", {
            "cost_type": "2",
            "item_id": str(item_id),
            "quantity": str(quantity),
        })

    def get_store_snapshot(self, deck_data: dict[str, Any] | None = None) -> dict[str, Any]:
        """Fetch store metadata with a harmless no-op commander assignment.

        Captured AT traffic shows setDeckCommander returns store_items while
        setActiveDeck does not. Re-applying the commander already attached to the
        active deck leaves the account selection unchanged and gives us the live
        Nixon-pack catalog without guessing product IDs.
        """
        deck_data = deck_data or self.get_exact_decks()
        ud = deck_data.get("user_data") or {}
        active = str(ud.get("active_deck") or "")
        decks = deck_data.get("user_decks") or {}
        row = None
        if isinstance(decks, dict):
            row = decks.get(active) or decks.get(int(active)) if active.isdigit() else decks.get(active)
        elif isinstance(decks, list):
            row = next((x for x in decks if isinstance(x, dict) and str(x.get("deck_id")) == active), None)
        row = row or {}
        commander = row.get("commander") or {}
        cid = commander.get("unit_id") or commander.get("commander_id") or commander.get("hero_id")
        if not active or cid is None:
            raise ATApiError("Could not determine the active deck/commander needed to read store metadata.")
        return self.call("setDeckCommander", {"commander_id": str(cid), "deck_id": str(active)})

    def call_profile(self, profile: dict[str, Any], values: dict[str, Any] | None = None) -> dict[str, Any]:
        """Execute a learned API protocol profile.

        Profiles are intentionally data-driven so Rumble/Siege/Swole/deck-selection
        calls can be learned from the live client instead of hard-coding guessed
        message names. A profile shape is {"message": str, "data": {..}} and
        string values may use Python-style {name} placeholders.
        """
        if not isinstance(profile, dict) or not profile.get("message"):
            raise ATApiError("Required API protocol has not been learned yet.")
        vals = values or {}

        def format_value(value: Any) -> Any:
            # Learned Siege traffic can carry combat_data as a nested object, not
            # only a flat POST field. Resolve placeholders recursively so the exact
            # captured shape can be reused for a different live target island.
            if isinstance(value, str):
                try:
                    return value.format(**vals)
                except Exception:
                    return value
            if isinstance(value, dict):
                return {str(k): format_value(v) for k, v in value.items()}
            if isinstance(value, list):
                return [format_value(v) for v in value]
            if isinstance(value, tuple):
                return [format_value(v) for v in value]
            return value

        payload: dict[str, Any] = {
            str(key): format_value(value) for key, value in (profile.get("data") or {}).items()
        }

        # 1.6-era learned profiles often captured one literal item/pack/card ID
        # instead of a placeholder. Keep those profiles useful by replacing only
        # semantically matching ID/quantity fields when the caller supplies a
        # runtime selection. This avoids the old "every button spends the same
        # item / buys the same pack" behavior without guessing new API calls.
        def replace_id(value_name: str, field_terms: tuple[str, ...]) -> None:
            if value_name not in vals or vals.get(value_name) in (None, ""):
                return
            for field in list(payload):
                low = str(field).lower()
                if "id" in low and any(term in low for term in field_terms):
                    payload[field] = str(vals[value_name])

        replace_id("item_id", ("item", "consum", "reward", "currency", "product", "offer"))
        replace_id("purchase_id", ("pack", "product", "offer", "store", "purchase", "item", "bundle"))
        replace_id("unit_id", ("unit", "card"))
        if vals.get("unit_index") not in (None, ""):
            for field in list(payload):
                if "unit_index" in str(field).lower(): payload[field]=str(vals["unit_index"])
        replace_id("mission_id", ("mission",))
        # Challenge-backed events expose two different identifiers in live AT data:
        # the outer event id (for example Swole event 900042) and the inner
        # challenge id (for example 1070). 1.7.9 treated both as challenge_id,
        # which can make a perfectly valid learned fight profile target the wrong id.
        replace_id("challenge_id", ("challenge",))
        replace_id("event_id", ("event",))
        replace_id("choice_id", ("choice", "reward", "card", "unit", "item"))
        replace_id("mail_id", ("mail", "message"))
        if vals.get("mail_type") not in (None, ""):
            for field in list(payload):
                if str(field).lower() in {"mail_type", "type"}: payload[field]=str(vals["mail_type"])
        replace_id("target_slot_id", ("slot", "island", "node", "position"))
        replace_id("target_location_id", ("location", "territory", "target", "enemy", "defender", "siege"))

        if vals.get("quantity") not in (None, ""):
            for field in list(payload):
                low = str(field).lower()
                if any(term in low for term in ("quantity", "amount", "count", "num")):
                    payload[field] = str(vals["quantity"])
        return self.call(str(profile["message"]), payload)

    def apply_selection(self, *, deck_id: str | None = None, hero_id: str | None = None, profiles: dict[str, Any] | None = None) -> None:
        # These calls are confirmed by captured live AT traffic; no learner dependency.
        active_deck = deck_id
        if deck_id:
            self.call("setActiveDeck", {"deck_id": str(deck_id)})
        if hero_id:
            if not active_deck:
                init = self.init(); active_deck = str((init.get("user_data") or {}).get("active_deck") or "")
            if not active_deck:
                raise ATApiError("Could not determine the active deck required for hero switching.")
            self.call("setDeckCommander", {"commander_id": str(hero_id), "deck_id": str(active_deck)})

    def get_exact_decks(self, init_data: dict[str, Any] | None = None) -> dict[str, Any]:
        init_data = init_data or self.init()
        active = (init_data.get("user_data") or {}).get("active_deck")
        if active is None:
            raise ATApiError("AT did not report an active deck ID.")
        return self.call("setActiveDeck", {"deck_id": str(active)})

    def get_hero_squad(self) -> dict[str, Any]:
        return self.call("getHeroSquad")

    def get_boost_level(self) -> dict[str, Any]:
        """Authoritative ad-boost state. Confirmed by captured live traffic."""
        return self.call("getBoostLevel")

    def get_guild_siege_status(self) -> dict[str, Any]:
        """Read the live Siege setup/attack state and both island maps."""
        return self.call("getGuildSiegeStatus")

    def set_siege_defense_deck(self, deck_id: str | int) -> dict[str, Any]:
        """Confirmed Siege defense call from the user's capture."""
        return self.call("setDeckType", {"deck_type": "1", "deck_id": str(deck_id)})

    def set_siege_defense_island(self, slot_id: str | int) -> dict[str, Any]:
        """Confirmed Siege defense-island assignment call from the user's capture."""
        return self.call("selectUserGuildSiegeLocation", {"slot_id": str(slot_id)})

    def start_swole_training(self, hero_id: str | int) -> dict[str, Any]:
        return self.call("startHeroTraining", {"hero_id": str(hero_id)})

    def start_challenge(self, challenge_id: str | int, level: int | None, *, auto_battle: bool = False) -> dict[str, Any]:
        """Start Swole/Clash/Melee/Challenge directly with fresh signatures."""
        if not str(challenge_id).isdigit() or level is None or int(level) < 0:
            raise ATApiError("A current challenge ID and level are required to start this fight.")
        return self.call("startChallenge", {"challenge_id": str(challenge_id), "level": str(int(level)),
                                             "auto_battle": "1" if auto_battle else "0"})

    def start_siege(self, slot_id: str | int) -> dict[str, Any]:
        """Use the public client's slot-only start shape and fresh signing.

        The runner must confirm Siege's battle phase and a live enemy island
        before calling this resource-consuming endpoint.
        """
        if not str(slot_id).isdigit() or int(slot_id) < 1:
            raise ATApiError("A live enemy Siege slot is required to start this fight.")
        return self.call("fightGuildSiege", {"slot_id": str(int(slot_id))})

    def complete_swole_training(self, task_index: str | int) -> dict[str, Any]:
        return self.call("completeHeroTraining", {"task_index": str(task_index)})

    @staticmethod
    def _swole_tasks_from(data: dict[str, Any] | None) -> dict[str, Any] | list[Any]:
        data = data or {}
        tasks = data.get("user_tasks")
        if tasks is None and isinstance(data.get("user_data"), dict):
            tasks = (data.get("user_data") or {}).get("user_tasks")
        return tasks or {}

    def get_swole_status(self) -> dict[str, Any]:
        """Robust Swole status assembled from confirmed live endpoints.

        getHeroSquad is authoritative for hero training levels. Active training
        tasks are not consistently present in getUserAccount on every client
        refresh, while init and startHeroTraining responses do expose user_tasks.
        We therefore merge both reads instead of incorrectly reporting idle.
        """
        squad = self.get_hero_squad()
        try:
            account = self.get_user_account()
        except Exception:
            account = {}
        try:
            init = self.init()
        except Exception:
            init = {}
        tasks = self._swole_tasks_from(account) or self._swole_tasks_from(init)
        now = account.get("time") or init.get("time") or squad.get("time")
        user_data = {}
        if isinstance(init.get("user_data"), dict): user_data.update(init.get("user_data") or {})
        if isinstance(account.get("user_data"), dict): user_data.update(account.get("user_data") or {})
        # Swole fight tickets live inside the active challenge row, not in the
        # normal user_data energy/stamina fields. Different AT responses surface
        # active_events in slightly different places, so search the account/init
        # trees rather than assuming init.active_events is always present.
        task_event_id = ""
        task_vals = tasks.values() if isinstance(tasks, dict) else tasks if isinstance(tasks, list) else []
        for t in task_vals:
            if not isinstance(t, dict):
                continue
            try:
                if t.get("task_type") is not None and int(t.get("task_type")) != 2:
                    continue
            except Exception:
                pass
            if t.get("task_id_2") not in (None, ""):
                task_event_id = str(t.get("task_id_2"))
                break

        def iter_event_rows(source: Any):
            seen: set[int] = set()
            def walk(obj: Any):
                oid = id(obj)
                if oid in seen:
                    return
                if isinstance(obj, (dict, list)):
                    seen.add(oid)
                if isinstance(obj, dict):
                    raw = obj.get("active_events")
                    rows = raw.values() if isinstance(raw, dict) else raw if isinstance(raw, list) else []
                    for row in rows:
                        if isinstance(row, dict):
                            yield row
                    # data_cache and compact wrapper responses can contain the same
                    # event row below another object. Keep the recursion bounded by
                    # traversing containers only; primitive payloads are ignored.
                    for value in obj.values():
                        if isinstance(value, (dict, list)):
                            yield from walk(value)
                elif isinstance(obj, list):
                    for value in obj:
                        if isinstance(value, (dict, list)):
                            yield from walk(value)
            yield from walk(source)

        swole_event = {}
        candidates = []
        for source in (account, init, squad):
            for er in iter_event_rows(source):
                name = str(er.get("name") or "")
                eid = str(er.get("id") or "")
                if "swole" not in name.lower() and not (task_event_id and eid == task_event_id):
                    continue
                candidates.append(er)
        # Prefer the row matching the active training task's event id; otherwise
        # use the first named Swole row.
        er = next((r for r in candidates if task_event_id and str(r.get("id") or "") == task_event_id), None)
        if er is None and candidates:
            er = candidates[0]
        if isinstance(er, dict):
            cd = er.get("challenge_data") if isinstance(er.get("challenge_data"), dict) else {}
            en = cd.get("energy") if isinstance(cd.get("energy"), dict) else {}
            def n(value):
                try: return int(value)
                except Exception: return None
            swole_event = {
                "id": str(er.get("id") or task_event_id or ""),
                "event_id": str(er.get("id") or task_event_id or ""),
                "challenge_id": str(cd.get("id") or er.get("challenge") or ""),
                "name": str(er.get("name") or cd.get("name") or "Swole"),
                "points": er.get("points"),
                "level": cd.get("level"),
                "energy": n(en.get("current_value")),
                "max_energy": n(en.get("max_value")),
                "battle_cost": n(en.get("battle_cost")) or 1,
                "display_name": str(en.get("display_name") or "Swole tickets"),
                "recharge_amount": n(en.get("recharge_amount")),
                "recharge_time": n(en.get("recharge_time")),
                "last_recharge_time": n(en.get("last_recharge_time")),
            }
        return {
            "time": now,
            "user_hero_squad": squad.get("user_hero_squad") or {},
            "user_hero_squad_bonuses": squad.get("user_hero_squad_bonuses") or {},
            "user_tasks": tasks,
            "user_data": user_data,
            "swole_event": swole_event,
            "account": account,
            "init": init,
        }

    def get_static_files(self) -> dict[str, Any]:
        return self.call("getStaticFiles")

    def start_arena_battle(self) -> dict[str, Any]:
        """Start one normal Arena battle without server-side auto battle.

        The user's captured live browser trace showed startHuntingBattle with no
        mode-specific POST fields. Authentication is added by call().
        """
        return self.call("startHuntingBattle")

    def arena_server_auto(self, auto_battle: int = 5) -> BattleSummary:
        data = self.call("startHuntingBattle", {
            "auto_battle": auto_battle,
            "target_user_id": 0,
        })
        return self._battle_summary("Arena", data)

    def adventure(self, mission_id: int = 180, auto_battle: int = 5) -> BattleSummary:
        data = self.call("startMission", {
            "auto_battle": auto_battle,
            "target_user_id": 0,
            "mission_id": mission_id,
        })
        return self._battle_summary("Adventure", data)

    def play_battlefield_card(
        self,
        *,
        battle_id: int | str,
        host_id: int | str,
        card_uid: int | str,
        card_uid2: int | str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "battle_id": str(battle_id),
            "card_uid": str(card_uid),
            "host_id": str(host_id),
        }
        if card_uid2 is not None:
            payload["card_uid2"] = str(card_uid2)
        return self.call("playBattlefieldCard", payload)

    @staticmethod
    def _static_url_candidates(static: Any) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        def walk(obj: Any, path: str = "", depth: int = 0):
            if depth > 6:
                return
            if isinstance(obj, dict):
                for k, v in obj.items():
                    p = f"{path}.{k}" if path else str(k)
                    if isinstance(v, str) and v.startswith(("http://", "https://")):
                        out.append((p.lower(), v))
                    else:
                        walk(v, p, depth + 1)
            elif isinstance(obj, list):
                for i, v in enumerate(obj):
                    walk(v, f"{path}[{i}]", depth + 1)
        walk(static)
        return out

    def _find_static_url(self, static: dict[str, Any], key: str) -> str | None:
        sf = static.get("static_files") or static
        if isinstance(sf, dict):
            direct = sf.get(key)
            if isinstance(direct, str) and direct.startswith(("http://", "https://")):
                return direct

        candidates = self._static_url_candidates(sf)
        if key == "card_data":
            terms = ("card_data", "carddata", "unit_data", "unitdata", "cards", "units")
        elif key == "item_data":
            terms = ("item_data", "itemdata", "items", "inventory", "reward", "currency")
        elif key == "hero_data":
            terms = ("hero_data", "herodata", "commander_data", "commanderdata", "heroes", "commanders")
        else:
            terms = (key.lower(),)

        scored: list[tuple[int, str]] = []
        for path, url in candidates:
            hay = f"{path} {url}".lower()
            score = 0
            for idx, term in enumerate(terms):
                if term in hay:
                    score = max(score, 100 - idx * 8)
            if key == "card_data" and any(x in hay for x in ("combo", "recipe")):
                score -= 25
            if score > 0:
                scored.append((score, url))
        scored.sort(reverse=True)
        return scored[0][1] if scored else None

    def _versioned_static_candidates(self, key: str) -> list[str]:
        """Build CDN candidates from the version hashes returned by init().

        AT's getStaticFiles response currently exposes only a subset of the static
        catalogs (notably cards). The live init response still contains version
        hashes for mission_data / campaign_data / item_data. The web client loads
        those catalogs from the same CDN, so try the small set of historically
        used filename forms instead of pretending the catalog does not exist.
        """
        try:
            init = self._init_snapshot or self.init()
        except ATNetworkError:
            raise
        except Exception:
            init = {}
        version = str((init or {}).get(f"{key}_version") or "").strip()
        if not version:
            req = (init or {}).get("request") if isinstance(init, dict) else None
            if isinstance(req, dict):
                version = str(req.get(f"{key}_version") or "").strip()
        if not version:
            return []

        prefixes = {
            "mission_data": ("missions", "mission_data", "mission"),
            "campaign_data": ("campaigns", "campaign_data", "campaign"),
            "item_data": ("items", "item_data", "item"),
            "hero_data": ("heroes", "hero_data", "commanders", "commander_data"),
        }.get(key, (key,))
        hosts = (
            "https://cb-cdn.synapsegames.com/data",
            "https://cb-cdn.synapse-games.com/data",
        )
        out=[]
        for host in hosts:
            for prefix in prefixes:
                out.append(f"{host}/{prefix}_{version}.json")
        return out

    def _download_static_catalog(self, key: str) -> dict[str, dict[str, Any]]:
        from .catalog_parser import normalize_static_catalog
        snapshot = self._init_snapshot or self.init()
        rows = normalize_static_catalog(snapshot.get(key), key)
        if rows:
            return rows
        network_failure=None
        try:
            static = self.get_static_files()
        except ATNetworkError as error:
            network_failure=error
            static={}
        except Exception:
            static = {}
        url = self._find_static_url(static, key)
        candidates = [url] if url else []
        # The live web client has the authoritative CDN URL even when
        # getStaticFiles omits mission/campaign catalogs. Pull that URL from the
        # dedicated browser's Performance resource list before guessing names.
        try:
            from .browser_bridge import discover_static_resource_urls
            candidates.extend(u for u in discover_static_resource_urls(key) if u not in candidates)
        except Exception:
            pass
        try:
            candidates.extend(u for u in self._versioned_static_candidates(key) if u not in candidates)
        except ATNetworkError as error:
            network_failure=error
        if not candidates:
            if network_failure is not None:raise network_failure from None
            raise ATApiError(f"AT did not expose a recognizable {key} static-file URL or version hash.")

        data = None
        errors=[]
        for candidate in candidates:
            try:
                r = self.session.get(str(candidate), timeout=self.timeout)
                if r.status_code == 404:
                    continue
                r.raise_for_status()
                obj = r.json()
                rows = normalize_static_catalog(obj, key)
                if rows:
                    data = rows
                    break
            except requests.exceptions.JSONDecodeError:
                errors.append("static fetch returned invalid JSON")
            except requests.RequestException as error:
                failure=_network_error(error)
                if network_failure is None or failure.retryable_read:
                    network_failure=failure
                errors.append("static fetch failed")
            except Exception:
                errors.append("static fetch failed")
            # Some event/static URLs are usable only from the already-running
            # web client context. Ask the live game browser before giving up.
            try:
                from .browser_bridge import fetch_json_resource_in_browser
                obj = fetch_json_resource_in_browser(str(candidate), timeout=min(25,self.timeout))
                rows = normalize_static_catalog(obj, key)
                if rows:
                    data=rows
                    break
            except ATNetworkError as error:
                if network_failure is None or error.retryable_read:
                    network_failure=error
                errors.append("browser static fetch failed")
            except Exception:
                errors.append("browser static fetch failed")
        if data is None:
            if network_failure is not None:raise network_failure from None
            detail = errors[-1] if errors else "no candidate URL returned JSON"
            raise ATApiError(f"Could not download AT {key}: {detail}")

        out: dict[str, dict[str, Any]] = {}
        def absorb(obj: Any, depth: int = 0):
            if depth > 4:
                return
            if isinstance(obj, list):
                for row in obj:
                    if isinstance(row, dict):
                        rid = row.get("id") or row.get("unit_id") or row.get("item_id") or row.get("card_id") or row.get("hero_id") or row.get("commander_id")
                        if rid is not None:
                            out[str(rid)] = row
                        elif depth < 3:
                            absorb(row, depth + 1)
            elif isinstance(obj, dict):
                # ID-keyed maps are common.
                for k, row in obj.items():
                    if isinstance(row, dict):
                        rid = row.get("id") or row.get("unit_id") or row.get("item_id") or row.get("card_id") or row.get("hero_id") or row.get("commander_id")
                        if rid is not None or str(k).isdigit():
                            out[str(rid or k)] = row
                        elif depth < 3:
                            absorb(row, depth + 1)
                    elif isinstance(row, list) and depth < 3:
                        absorb(row, depth + 1)
        absorb(data)
        return out

    def card_catalog(self) -> dict[str, dict[str, Any]]:
        return self._download_static_catalog("card_data")

    def item_catalog(self) -> dict[str, dict[str, Any]]:
        return self._download_static_catalog("item_data")

    def hero_catalog(self) -> dict[str, dict[str, Any]]:
        return self._download_static_catalog("hero_data")

    def campaign_catalog(self) -> dict[str, dict[str, Any]]:
        return self._download_static_catalog("campaign_data")

    def mission_catalog(self) -> dict[str, dict[str, Any]]:
        return self._download_static_catalog("mission_data")

    def combo_catalog(self) -> dict[str, Any]:
        """Read the current public recipe map from its authoritative manifest URL."""
        files=self.get_static_files().get("static_files") or {}
        url=files.get("combo_data_compressed")
        if not isinstance(url,str) or not url.startswith("https://cb-cdn.synapsegames.com/"):
            raise ATApiError("AT did not expose the current public combo recipe file.")
        try:
            response=self.session.get(url,timeout=self.timeout);response.raise_for_status();data=response.json()
        except requests.exceptions.JSONDecodeError:
            raise ATApiError("Public combo catalog returned invalid JSON.") from None
        except requests.RequestException as error:
            raise _network_error(error) from None
        except ValueError:
            raise ATApiError("Public combo catalog returned invalid JSON.") from None
        if not isinstance(data,dict):raise ATApiError("The public combo catalog has an unexpected format.")
        return data.get("combo_data_compressed") or data

    @staticmethod
    def extract_battle_data(response: dict[str, Any]) -> dict[str, Any] | None:
        """Find the active battle state across observed AT response shapes.

        Most modes return top-level ``battle_data``. Some event endpoints wrap
        that object one level deeper or return the battle object directly. Keep
        the lookup conservative: a direct object is accepted only when it has a
        battle_id plus normal battle-state fields.
        """
        if not isinstance(response, dict):
            return None
        seen: set[int] = set()
        def walk(obj: Any, depth: int = 0) -> dict[str, Any] | None:
            if depth > 6 or not isinstance(obj, dict):
                return None
            oid=id(obj)
            if oid in seen:
                return None
            seen.add(oid)
            bd=obj.get("battle_data")
            if isinstance(bd, dict):
                return bd
            if obj.get("battle_id") is not None and any(k in obj for k in ("hand", "card_map", "battlefield", "host_id", "enemy_cards", "turn")):
                return obj
            # Prefer battle-looking wrappers before scanning every nested dict.
            for k in ("battle", "current_battle", "battle_state", "data", "result", "response"):
                v=obj.get(k)
                if isinstance(v, dict):
                    hit=walk(v, depth+1)
                    if hit:
                        return hit
            for v in obj.values():
                if isinstance(v, dict):
                    hit=walk(v, depth+1)
                    if hit:
                        return hit
            return None
        return walk(response)

    @staticmethod
    def battle_finished(response: dict[str, Any], battle_data: dict[str, Any] | None) -> bool:
        if not response:
            return True
        if not isinstance(battle_data, dict):
            return True
        if battle_data.get("results"):
            return True
        if battle_data.get("battle_finished"):
            return True
        # Some versions expose winner only at completion.
        if battle_data.get("winner") is not None:
            return True
        return False

    @staticmethod
    def extract_outcome(response: dict[str, Any], battle_data: dict[str, Any] | None) -> bool | None:
        """Best-effort W/L extraction across observed AT response shapes."""
        sources: list[Any] = []
        if isinstance(battle_data, dict):
            sources.extend([battle_data.get("winner"), battle_data.get("results")])
        if isinstance(response, dict):
            sources.extend([response.get("winner"), response.get("results"), response.get("battle_results")])

        def parse(value: Any) -> bool | None:
            if isinstance(value, bool):
                return value
            if isinstance(value, (int, float)):
                if value == 1:
                    return True
                if value == 0:
                    return False
            if isinstance(value, str):
                v = value.strip().lower()
                if v in {"win", "won", "victory", "player", "attacker", "true", "1"}:
                    return True
                if v in {"loss", "lost", "defeat", "opponent", "defender", "false", "0"}:
                    return False
            if isinstance(value, dict):
                for k in ("won", "win", "winner", "victory", "result"):
                    if k in value:
                        p = parse(value.get(k))
                        if p is not None:
                            return p
            if isinstance(value, list):
                for row in reversed(value):
                    p = parse(row)
                    if p is not None:
                        return p
            return None

        for src in sources:
            p = parse(src)
            if p is not None:
                return p
        return None

    @staticmethod
    def opponent_name(battle_data: dict[str, Any] | None) -> str:
        if not isinstance(battle_data, dict):
            return "Unknown"
        return str(
            battle_data.get("enemy_name")
            or ((battle_data.get("kv_store") or {}).get("enemy_name") if isinstance(battle_data.get("kv_store"), dict) else "")
            or "Unknown"
        )

    @staticmethod
    def _battle_summary(mode: str, data: dict[str, Any]) -> BattleSummary:
        results = ((data.get("battle_data") or {}).get("results") or [])
        if not results:
            return BattleSummary(mode=mode, raw=data)
        last = results[-1] if isinstance(results[-1], dict) else {}
        opponent = ((last.get("opponent") or {}).get("name") if isinstance(last.get("opponent"), dict) else None) or "Unknown"
        winner = last.get("winner", "Unknown")
        return BattleSummary(mode=mode, opponent=str(opponent), winner=str(winner), raw=data)
