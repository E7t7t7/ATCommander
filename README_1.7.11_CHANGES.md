# AT Commander 1.7.11

## Built from the user's 2026-09-28 local captures

### Swole fight repair
- The successful Swole capture proves `startChallenge` needs the live inner `challenge_id` **and the live challenge level**. The capture used `challenge_id=1070`, `level=82`, `auto_battle=0` and returned a valid Swole battle.
- 1.7.10 fixed event-vs-challenge IDs but could still replay the captured literal level forever. 1.7.11 now replaces the learned `level` with `challenge_data.level` before every Swole fight.
- Ticket detection remains based on `challenge_data.energy`; training scheduling logic is otherwise unchanged.

### Challenge / Clash / Melee repair
- The successful Finger Cutter capture used `startChallenge(challenge_id=1101, level=17, auto_battle=0)`.
- The old learned profile permanently stored `level=17`, causing later runs/events to fail as the live event level changed.
- Detected event rows now retain live challenge level, energy, max energy and battle cost.
- Before every Challenge/Clash/Melee battle, Commander refreshes the live event row and substitutes the current level. Failures now report challenge id, level and AT result instead of only saying "no battle_data".

### Claim Mail repair
- The user's learned Claim Mail profile stored the literal old `mail_id=2946`. Replaying that cannot function as Claim All after that message is consumed.
- `CLAIM ALL MAIL` is now built in. Commander reads the current `user_mail`, selects current reward-bearing unclaimed messages, and calls `claimMessageRewards` with each message's actual `mail_id` and `mail_type`.
- The old captured mail ID is no longer used for Claim All.

### Item and Nixon spending
- Large `useItem(quantity=N)` operations were unreliable (for example a large Golden Turd pull count). Non-choice resources are now spent as real one-pull requests distributed across bounded parallel workers instead of one giant quantity request.
- Choice-style resources remain one pull at a time for safety until their reward-selection endpoint is proven.
- Nixon buying keeps one real purchase per request but reuses one client per worker and now supports up to 12 workers (default 6) for large runs.
- Added a visible Inventory progress bar and readable `done/total` status for item pulls, Nixon packs and mail claims.
- Removed raw API JSON dumps from normal item-spend completion messages.

### Known/open items
- Siege target A/B/C selection is confirmed. The live 2026-09-28 Siege window ended with direct/replayed `fightGuildSiege(slot_id=...)` still rejected as `Invalid combat data`. Keep it parked until next Siege rather than burning attacks guessing.
- Daily Upgrade capture still sees only analytics traffic in the user's latest trace; no verified AT upgrade mutation endpoint has been recovered. Do not claim it is fixed.
- Adventure Main stage farming works. Temporary/event campaign-to-stage discovery is still incomplete for Anniversary-style islands.
- Delete Mail remains intentionally separate until Claim Mail is proven in 1.7.11.
- Choice-item auto-selection priority requested by user: **Rogu first → other Characters → Items**, then choose the most valuable option within the lower tiers. The final choice endpoint is not yet proven, so this is recorded for the next implementation pass.

### Preserved / do-not-regress
- Arena API battle automation is proven working.
- Swole training milestone scheduling and Arena+Swole concurrency are proven working.
- Rumble battle automation is proven working.
- Ad calibration/replay works once the game itself is already open; the recorded click sequence and Play Now coordinate remain supported.
