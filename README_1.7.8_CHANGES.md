# AT Commander 1.7.8 changes

## Siege: browser-signed start instead of rebuilding `fightGuildSiege`

The 1.7.7 live test proved three separate facts:

- A/B/C target selection is correct.
- A successful manual Siege start exposes only `slot_id` as the action-specific field.
- Rebuilding that same minimal request through Python is rejected by AT as `Invalid combat data.`

1.7.8 therefore stops treating Siege as a normal direct-HTTP start call. During Siege learning, Commander now stores the browser target/request IDs for the successful `fightGuildSiege` XHR. At runtime it replays that browser request through Chromium's own network stack and swaps only the live target slot for A → B → C. The API battle engine still takes over after a valid `battle_data` response.

A migration step reads the existing local `learn_siege_sanitized.jsonl` created by 1.7.7 and adds its request ID to the current Siege profile when possible, so the user should not have to burn another Siege attack merely to teach the same request again. If the old browser request is no longer replayable because the browser session changed, Commander reports that explicitly and requests one new successful learn instead of sending repeated invalid API calls.

## Ads: teach Play Now from the real Windows cursor

The calibrated ad sequence itself is now confirmed to work when the game canvas is already open. The remaining failure was launching through Kongregate's Play Now control.

`TEACH PLAY NOW` no longer depends on DOM/pointer-event capture. It now:

1. opens/reuses the fixed no-scroll browser,
2. brings it to the foreground,
3. gives a four-second countdown,
4. saves the actual Windows cursor position while the user hovers over Play Now.

Future ad runs click that exact screen point with native Windows mouse input. While waiting for the game canvas, Commander can repeat only this taught native click at a safe interval; it does not scroll or resume broad text matching.

The existing calibrated ad flow remains unchanged because the user's test confirmed that native replay can actually watch an ad once the game is open. Commander still verifies credit with `getBoostLevel` and does not fabricate ad completion.

## Adventure: discover the real mission/campaign JSON URLs from the live browser

The guessed CDN filename forms still failed to expose Anniversary stages. 1.7.8 adds a more authoritative path: when Adventure Refresh needs an event map, Commander can open/reuse the live game window and inspect the browser's Performance resource list for the exact mission/campaign JSON URLs the client loaded.

Those URLs are cached locally and used ahead of filename guesses. Refresh logging now reports how many mission and campaign rows were actually recovered. Event islands continue to show human-readable Stage labels while raw mission IDs remain internal.

## Preserved working behavior

- Arena automation remains unchanged.
- Swole training scheduling/monitoring remains unchanged.
- Arena + Swole concurrency remains unchanged.
- Main Adventure farming remains unchanged.
- Siege target priority A → B → C + any-living-island fallback remains unchanged.
