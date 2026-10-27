# AT Commander 1.7.20 changes

## Combat start: Fetch, not XHR
Live 1.7.19 diagnostics finally proved the successful Swole/Clash `startChallenge` request is Chromium resource type **Fetch**, initiated by `b.fetchWithProgress`, and includes fresh `client_time`, `clisig`, and `msgsig` fields.  The prior native replay path incorrectly attempted `Network.replayXHR`, which explains why a request could be captured successfully and then immediately be reported stale/missing.

1.7.20 changes the strategy:
- Live combat capture also records the harmless trusted fight-button click coordinates inside the dedicated mini-game target.
- For Fetch-based Swole/Challenge starts, Commander arms a network listener and replays that **game UI click with CDP Input** (no Windows cursor movement).
- The real game client therefore constructs the new `startChallenge` Fetch request and its fresh signatures itself.
- `Network.replayXHR` remains only as a legacy path for captures Chromium actually reports as XHR.
- Passwords, Kong tokens, `clisig`, `msgsig`, cookies, and raw signed request bodies are still never persisted in the learned profile.

A new capture is required once because older 1.7.17/1.7.19 profiles do not contain the fight-button click coordinates.

## CM Reviews: recent Reddit consensus layer
The 1.7.19 current-power scorer remains the foundation. 1.7.20 adds a second, bounded community-evidence layer:
- Searches recent/public r/AnimationThrowdown posts for the requested combo.
- Extracts explicit 0–10 CM review scores, including dated historic-review rows when available.
- Weights ratings strongly by recency.
- Current AT card/skill values remain dominant; Reddit can only nudge the final rating by a limited amount.
- Large disagreement is shown explicitly as a caution instead of silently averaging incompatible eras.
- The desktop review and Discord bot use the same consensus logic.
- Results are cached locally in memory for 30 minutes to avoid hammering Reddit.
- If Reddit blocks/rate-limits the request, the current-data review still works normally.

This intentionally treats old Inertigo/Samuel/etc. scores as context, not timeless truth. A six-month-old 6/10 cannot override a present-day same-skill benchmark showing severe power creep.

## Validation target
For CM scoring, Wingnut Amy should remain near the bottom from current values even if old Reddit history is much higher; the review should explain the disagreement. Buckarito should remain materially stronger.
