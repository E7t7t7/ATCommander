# AT Commander 1.7.14

Built from the user's live 1.7.13 results.

## Challenge / Swole browser context
- Fixes the 1.7.13 `TypeError: Failed to fetch` fallback by no longer assuming Kongregate's outer page is the game request origin.
- Browser request replay now ranks the AT/Synapse game targets first and can execute inside the actual game iframe isolated world.
- Existing live challenge IDs and levels are still refreshed normally.

## Adventure event missions
- Event mapping from 1.7.13 is retained. Anniversary correctly resolves to the live mission marker (9040 in the user's test).
- If standalone `startMission` returns no battle state, Adventure now retries that exact mission start from the live game browser context and reports the browser response if it still fails.

## Nixon bulk purchases
- `expected_cost` now reflects unit cost × quantity for a bulk `buyStoreItem` request.
- Commander attempts the full requested quantity in one server request first (for example 500 Basic Packs).
- Only an explicit server rejection is split into smaller quantities; unknown network failures are not retried to avoid duplicate purchases.
- Parallel workers are no longer used for Nixon bulk buying.

## Golden Turds
- Replaced six simultaneous `useItem(quantity=1)` calls with one sequential `useItem(quantity<=6)` request at a time.
- Explicitly rejected batches are automatically split smaller.
- This is intended to be both faster and less likely to trip the >6 live failure the user observed.

## Ad watching
- Ad/game helper geometry is now 640×720 instead of 1280×900.
- Automated ad playback never moves or clicks the Windows cursor. All playback uses Chromium DevTools input scoped only to Commander's dedicated game window.
- Saved Play Now coordinates are replayed inside that browser window rather than through native desktop input.
- Legitimate boost verification via `getBoostLevel` remains required; no ad-completion API is fabricated.
