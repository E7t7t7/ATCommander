# AT Commander 1.7.16 changes

Built from the user's live 1.7.15 results on 2026-09-28.

## Nixon / Golden Pack purchasing

Live 1.7.15 testing proved both endpoints are valid: the first purchase succeeds, then parallel/rapid follow-up calls are rejected with `Please try that again in a moment / warning_16_text`.

1.7.16 changes the transport instead of increasing workers:

- Nixon now tries the entire requested stack as **one native `buyStoreItem(quantity=N)` request first**.
- `expected_cost` stays the selected pack's **unit price**. 1.7.14's experimental bulk path multiplied expected cost by quantity and did not match the game's known single-pack request shape.
- Golden Pack now also tries one direct `buyStoreItem(item_id=63, quantity=N)` request first.
- If AT explicitly rejects a quantity for a non-rate reason, Commander splits only that rejected amount into smaller chunks.
- If AT returns `warning_16`, Commander waits about four seconds and retries the same rejected chunk rather than aborting after one success or hammering several parallel lanes.
- After a successful fallback chunk, the next chunk respects the same server cooldown.
- Unknown network timeouts are not blindly replayed, avoiding accidental duplicate purchases.

The old public AT purchase scripts also use one-pack `buyStoreItem` calls followed by a four-second sleep, which matches the live `warning_16` behavior observed here. Native quantity mode is attempted first because the user's in-game bulk purchase is dramatically faster than one-pack API loops.

## Ads / mini game browser

- The reusable AT browser now launches as a compact **640x720 Edge app window** instead of a normal tabbed Edge window.
- The old absolute Windows-cursor Play Now calibration is ignored by 1.7.16.
- **TEACH PLAY NOW** now arms a browser pointer recorder. The user clicks Play Now once manually inside the mini browser; only the normalized in-browser coordinate is saved.
- Automated Play Now and ad-sequence playback use Chromium DevTools input only. They do not move or click the Windows cursor.
- Existing ad click-sequence calibration remains reusable once the mini browser reaches the game canvas.

## Swole / Challenge / Clash / Melee

Repeated live tests proved both reconstructed `requests.Session` and reconstructed browser `fetch` calls reach AT but are rejected as `Invalid combat data / warning_157_text`.

1.7.16 stops sending those already-proven-bad fallback requests. It now:

- tries only the exact native Chromium `startChallenge` request when a current-session request id exists;
- immediately reports when that request id is stale/missing;
- asks for **one** fresh native capture in the currently-open mini AT browser rather than wasting additional automated start attempts;
- preserves queued Swole fight steps when capture is required.

A fresh capture is still needed to determine whether AT's combat signature survives updating the live challenge level. If a fresh exact-XHR replay itself returns warning_157, do not repeat captures; that result proves the next fix must invoke the live game client's dispatcher/signing path instead.

## Deliberately unchanged

- Arena
- Rumble
- Swole training scheduler
- Claim/Delete Mail
- normal Main Adventure farming
- item-choice safety priority
