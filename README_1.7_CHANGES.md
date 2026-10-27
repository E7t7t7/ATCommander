# AT Commander 1.7

Built from the verified 1.6 source base in the 2026-09-27 handoff.

## Fixed in 1.7
- Saved deck selectors now use the authoritative `user_decks` returned by a no-op `setActiveDeck` refresh. Exact account deck names are preserved.
- Arena trophies now use only `pvp_data.rating` / `common_fields.pvp_rating`; rank/badge labels are no longer treated as trophies.
- Arena energy refresh uses `common_fields.current_pvp_energy` with `user_data.stamina` fallback before a battle starts.
- Ad status uses `user_data.boost_level` / `boost_end_time`; no fabricated ad-completion endpoint is implemented.
- Hero selectors are restricted to `getHeroSquad` entries with `unlocked == 1`. Names are resolved only from hero/static data, never arbitrary global IDs.
- Deck and hero switching use confirmed `setActiveDeck` and `setDeckCommander` calls directly.
- Swole uses the confirmed split: `getHeroSquad` status, `startHeroTraining(hero_id)`, and `completeHeroTraining(task_index)`.
- Inventory no longer renders every owned card. Cards are summarized by rarity counts.
- Stats views default to changed-only so unchanged resources do not dominate gains views.
- Rumble/API battle engine and Item -> Combo ordering were left intact.

## Still intentionally not guessed
- Rumble defense-set call (capture did not contain it).
- Siege battle-start call if no valid learned profile exists.
- Adventure refresh/progress endpoint.
- Mail claim/delete, Nixon purchase choices, fuse/upgrade workflows beyond confirmed captures.

The learning browser remains available for actions that genuinely still require a live capture.
