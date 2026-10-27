# AT Commander 1.7.2 — focused recovery patch

Built from the working 1.7.1 line plus the user's 2026-09-27 screenshots and sanitized captures.

## Confirmed working / preserved

- Arena still uses the hard Item -> immediate Combo -> Item -> Combo sequence.
- Exact account deck names remain sourced from `user_decks`.
- Arena SFC rating uses the real rating field, not rank/badge labels.
- Pre-battle Arena energy and `getBoostLevel` ad state remain live.
- Rumble and Siege now have explicit offense deck + offense hero selectors as well as defense deck controls.

## Ads

- Added visible **WATCH ADS** controls and **Auto-watch ads before any automation if boost is inactive**.
- Ad state is verified through `getBoostLevel`.
- Legitimate ad automation uses the real AT browser/UI path and waits for the boost to become active.
- AT Commander never calls/replays `recordAdBoost` to fabricate ad completion. If the game's ad button exists only inside a canvas and cannot be clicked safely, Commander reports that instead of spoofing it.

## Inventory / resources

- Normal inventory presentation is now compact and human-readable: card rarity totals plus named resources/materials.
- Known live resource IDs are mapped to names (Nixons, Gems, Giggitywatts, stones, Arena refills, Research Speedups, season passes, Swole currency, Ad Crate, expanders, etc.).
- Unresolved numeric IDs are hidden unless **Show unresolved IDs** is checked.
- The Nixon purchase control is populated from live `store_items` when available, with known Basic / Rare Basic / Epic Basic / Giggitywatts options as a fallback.
- Nixon purchases no longer reuse a frozen learned product. They execute the selected live store item and expected Nixon cost.
- Optional post-purchase trash recycle is available. It protects every saved-deck card and all foils, and only considers loose level-1 Common/Rare/Epic cards selected by the user.
- **Spend Selected Item** uses the actually selected inventory item ID instead of a frozen learned item. Non-consumables are allowed to fail safely at the AT API rather than being guessed.

## Swole

- Status now reads `getHeroSquad` plus `getUserAccount.user_tasks`; it no longer relies on `init` for the active training timer.
- Training start and completion are split correctly.
- Added ordered schedules containing hero milestones, fight blocks, day labels, timed waits, and `WAIT TRAINING`.
- Paste Schedule accepts patterns such as:
  - `HERO #1 = Peter`
  - `DAY 1`
  - `TRAIN HERO #1 0->1`
  - `TRAIN Stewie -> 3`
  - `ATK 4 TIMES`
  - `WAIT TRAINING`
  - `WAIT 13h`
- Swole fight execution is wired to the same Item -> Combo engine, but the start-fight API still needs one real Swole fight capture when the event is live. No endpoint is invented.

## Adventure

- Removed the unusable flat pseudo-mission list.
- Main Adventure is selected as Island -> Stage; e.g. **3-1 maps to mission 107**.
- Custom/event mission IDs can be farmed explicitly.
- The selected stage uses the real `startMission` battle path with the same Item -> Combo battle engine.
- Refresh now reads confirmed Adventure preference/status fields instead of scanning arbitrary `init` integers.
- **Important:** the current supplied captures still do not expose a confirmed per-stage star/completion table. Auto-detect-incomplete therefore remains intentionally disabled rather than falsely declaring stages complete/incomplete. One clean progress response is still needed to implement that part safely.

## Still awaiting evidence

- Mail claim/delete: user had no mail to capture/test yet.
- Daily card upgrade/fuse: the supplied evidence still does not contain the actual upgrade/fuse mutation endpoint. Commander will not invent it. The learning detector has been strengthened for the next clean manual capture.
- Adventure per-stage completion/stars: one response that actually contains per-stage progress is still needed.
- Swole fight start: one live Swole fight-start capture is still needed.
