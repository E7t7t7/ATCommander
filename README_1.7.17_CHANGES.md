# AT Commander 1.7.17 changes

Built from the user's live 1.7.16 results on 2026-09-29.

## Nixon purchasing

The user's 212-pack live test completed fully with no errors, so the native stack-first purchase architecture is preserved.

- Nixon still tries the requested stack through the proven `buyStoreItem` path first.
- The run now reports exact elapsed seconds and effective packs/second when it completes.
- No new worker storm or speculative endpoint change was added.

## Golden Packs / Golden Turds

The user confirmed the live game itself purchases Golden Packs one at a time. 1.7.17 now mirrors that behavior instead of pretending Golden Packs are a true quantity-stack product.

- Every Golden Pack request uses `buyStoreItem(item_id=63, quantity=1)` on one persistent API session.
- Initial pacing is intentionally aggressive.
- `warning_16 / Please try that again in a moment` is treated as a non-purchase and retries the same pack after increasing the delay.
- Long clean success streaks gradually reduce the delay again.
- Progress shows done/total, current effective rate, and the adaptive delay.
- Completion reports elapsed time, packs/second, warning_16 backoff count, and final learned delay.

## Swole + Challenge / Clash / Melee live capture

The 1.7.16 test did not prove exact-XHR replay because the saved request IDs were already stale when automation ran, even though the mini browser itself remained open.

1.7.17 removes the awkward Diagnostics Start/Finish capture dance for these two modes:

- Swole tab adds **ARM LIVE FIGHT CAPTURE**.
- Challenge/Clash/Melee adds **ARM LIVE START CAPTURE**.
- The button immediately attaches a live CDP listener to the already-open mini browser.
- The user manually starts exactly one fight while the listener is armed.
- Commander captures the next successful `startChallenge` request and its request ID immediately in that same Chromium session.
- It waits for the response and installs the request only if AT returned a successful battle start.
- The log reports the request resource type, non-secret field names, and a safe initiator hint. This is specifically to reveal whether the live client is adding a combat/signature-related field that the older learner hid.

This is still intentionally a one-manual-start diagnostic bridge. If a truly fresh native request replays and AT still returns `warning_157`, the next implementation must invoke the game's own start dispatcher/signing path rather than replaying or reconstructing HTTP.

## Ads / mini browser

AT Commander continues to require the real rewarded-ad flow. It does not fabricate an ad view or send a reward/completion call while displaying a placeholder.

The mini-browser path is simplified to reduce the recurring Play Now problem:

- Reuse a loaded game canvas immediately when one exists.
- Try the real DOM/accessibility Play Now control once.
- No blind calibrated coordinate loop and no native Windows cursor fallback.
- If Kongregate still does not load the canvas, Commander fails cleanly and asks for one manual Play Now click in the mini browser; leave that mini browser open afterward.
- Once the canvas is loaded, recurring calibrated ad clicks remain browser-scoped and do not move the Windows cursor.

## Deliberately unchanged

- Arena
- Rumble
- Swole training scheduler
- Claim/Delete Mail
- normal Main Adventure farming
- item-choice safety priority
