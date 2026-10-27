# AT Commander 1.7.15 changes

Built from the user's live 1.7.14 test logs on 2026-09-28.

## Swole / Challenge / Clash

- Live tests proved both the standalone request and reconstructed browser-context `startChallenge` reach AT but are rejected with `Invalid combat data / warning_157`.
- Commander now preserves Chromium request IDs and target IDs for successful learned Swole/Challenge starts and can replay the exact browser XHR envelope while changing only the live challenge/event/level fields.
- Existing local sanitized learn traces are migrated automatically on startup when they contain recoverable browser request metadata. No new capture is requested merely for migration.
- Fixed learner templating so challenge IDs and event IDs stay separate instead of being collapsed into one placeholder.
- If Chromium has discarded the old request ID, the log explicitly asks for one fresh successful manual start in Diagnostics; repeated blind standalone tests are not presented as a fix.

## Adventure event islands

- `current_campaign_*` values are no longer treated as mission IDs. The Anniversary marker `9040` was proven invalid by AT (`This mission does not exist`).
- A live event with no trustworthy static mission map now displays `stage mapping needed` instead of silently filling the marker into Mission ID.
- Teach `Adventure battle` once while that event is selected. Commander stores the captured real `mission_id` against that event and reuses it on later refreshes.
- Learned Adventure starts also retain native browser replay metadata for future event-specific cases.

## Nixon packs

- Removed the failed giant-quantity experiment from 1.7.14.
- Fast buying now uses persistent API sessions with up to six rapid lanes, one real pack per `buyStoreItem` call, matching the proven shop request shape.
- Explicit AT busy/rate-limit rejections get bounded adaptive backoff/retry instead of making a large run die immediately; unknown network failures are not blindly retried.
- Progress and the exact completed count are always surfaced so a failure can no longer look like the button did nothing.

## Golden Turds / Golden Pack

- Golden Turds are no longer sent through `useItem`.
- Golden Pack uses the known direct shop request: `buyStoreItem`, `cost_type=2`, `item_id=63`, `quantity=1`.
- No Golden-Pack learning capture is required.
- Large Golden Pack runs use the same persistent, bounded fast-lane model with adaptive handling of explicit rate/busy responses.

## Mail / ads / confirmed systems

- Claim/Delete Mail remains unchanged; the user confirmed both work.
- The mouse-free dedicated mini ad browser from 1.7.14 remains in place; no return to global Windows mouse clicks.
- Arena, Rumble, Swole training, and normal Main Adventure farming are not rewritten or placed back into regression testing.
