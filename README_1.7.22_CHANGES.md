# AT Commander 1.7.22 changes

## Background combat signer — no manual fight initialization

1.7.20 proved that the real AT client can generate valid signed `startChallenge` Fetch requests. 1.7.21 hid the helper too early and still required the user to manually burn/finish a teaching fight, which defeated the point.

1.7.22 changes the role of the mini game. Once a Fight-button position has been learned, **ARM LIVE FIGHT CAPTURE is recovery-only**. Normal Swole and Challenge/Clash/Melee startup no longer requires a fresh request id or a manual fight.

For Fetch-based combat starts Commander now uses CDP response interception:

1. keep the dedicated game renderer alive off-screen,
2. inject the learned Fight click without moving the Windows cursor,
3. allow the real signed request (`client_time`, `clisig`, `msgsig`) to reach AT,
4. read the real battle response for Commander,
5. substitute a harmless response back to the hidden game so it stays parked on the pre-fight screen.

This makes the helper a signing oracle rather than a second battle UI and is intended to support consecutive unattended fights from the same pre-fight screen. A new **BACKGROUND GAME** button moves the loaded helper off-screen without starting any fight. If the game UI changes and the stored Fight coordinates stop working, the existing ARM capture button repairs the position once.

## CM Reviews

The review detail now begins with an explicit evidence-basis line explaining that AT Commander synthesizes all available current AT/card data, same-skill power benchmarks, account/mastery context when exposed, and recent Reddit/community ratings. This does not claim a live ChatGPT/API call; the local review/Discord engine remains deterministic and auditable.

## Unchanged

The 1.7.19 current-power scoring model and 1.7.21 Reddit JSON + Atom/RSS consensus paths are retained. Swole training scheduling logic is unchanged; the reported empty hero dropdown eventually populated and was not treated as a confirmed bug.
