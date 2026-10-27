# AT Commander 1.7.4 — Siege / ads / capture recovery

This build is based on the working 1.7.3 tree. It deliberately leaves the confirmed Arena engine and the confirmed Swole training scheduler alone.

## Ads: real browser UI, not fabricated completion

The old `WATCH ADS` routine searched DOM text for words such as `ad`, which could click the entire Kongregate wrapper and then fail once the game rendered inside a canvas. 1.7.4 removes that behavior.

- `CALIBRATE ADS` opens/reuses the persistent Learning Browser.
- Commander clicks only an exact `Play Now` / `Play Game` / `Play` control if the game has not started.
- Perform one normal ad manually during calibration: in-game ad box → Watch Ad → wait for the still-image/ad timer → close X.
- Commander records only normalized browser/canvas click coordinates and the delays between those clicks.
- `WATCH ADS` can then replay that same legitimate UI path up to three times.
- After every attempt, success is accepted only when `getBoostLevel` reports a higher boost level.
- Commander never calls `recordAdBoost` directly and never fabricates an ad completion.

The one-time calibration exists because the AT controls are canvas-rendered and are not exposed as reliable DOM buttons. If the game UI moves materially, recalibrate.

## Siege: unattended target priorities

The Events tab now separates defense placement from attack targeting.

- Defense deck selector.
- Defense island selector using the confirmed `selectUserGuildSiegeLocation` call.
- Offense deck and offense hero selectors.
- Target A, Target B and Target C dropdowns populated from the enemy island map returned by live Siege status.
- Optional `any living island` fallback when A/B/C are destroyed.
- Configurable polling interval.
- `ARM / AUTO-COMPLETE SIEGE` can be started during setup. It applies the selected defense configuration, polls Siege status, waits past the observed setup phase, then attacks the first living priority island once the attack phase is live.
- After each battle it refreshes Siege status so a destroyed priority automatically falls through to the next one.

The user's setup capture proves phase 1 is setup. 1.7.4 therefore does not invent a specific unobserved attack-phase number; it waits through phase 0/1 and treats a later phase with attacks available as live.

One live Siege battle-start request is still required if the local learned profile does not already exist. The armed monitor reloads the profile on every poll, so it does not need to be restarted after that first capture.

## Upgrade Card capture

The previous recorder could still report `No useful AT API request` even when the user successfully upgraded a card in the game. 1.7.4 broadens the isolated recorder substantially:

- attaches to pages, iframes, workers, service workers and shared workers;
- explicitly requests omitted POST bodies from Chromium;
- merges late POST bodies and request data echoed in the response;
- watches isolated XHR/fetch mutations even when they do not use the previously assumed AT API URL;
- recognizes `message`, `action`, `command` and `cmd` action fields;
- keeps non-replayable endpoint/path hints as diagnostics instead of pretending they are valid API messages.

Once one real Upgrade Card call is learned, `DO 3 DAILY UPGRADES` reads the live Daily Upgrade Cards quest progress and performs only the remaining amount (maximum three). It chooses loose level-1 cards, protects saved-deck cards and foils, and refuses Mythic cards.

## Adventure refresh/status

`Adventure status` is now built in rather than a Learn action. `REFRESH STATUS` updates:

- current / maximum Adventure energy;
- the selected island/stage target;
- last-played and auto-battle mission markers exposed by AT;
- campaign markers exposed by player preferences;
- every campaign/island for which the static catalog exposes mission rows.

Main Adventure remains explicit Island → Stage (`3-1` maps to mission 107 from the confirmed capture). Event/custom mission IDs remain available.

AT's captured account responses still do not expose a trustworthy per-stage three-star completion table, so 1.7.4 does not claim to auto-detect 100% completion. The farm target remains user-selectable.

## Preserved working behavior

Do not re-test these unless a regression appears:

- Arena battles and hard Item → immediate Combo sequence.
- Exact account deck list and unlocked hero list.
- Real SFC trophy field and pre-battle Arena energy.
- Swole training schedule execution and monitoring.
- Arena + Swole concurrent operation.
- Nixon purchase selection / parallel buying.
- Spend-item pull-count versus resource-cost handling.

## Focused tests for 1.7.4

1. **Ads:** run `CALIBRATE ADS` once, perform one normal ad, then press `WATCH ADS`. Confirm it clicks Play Now if needed, repeats the calibrated ad flow, and reaches the expected boost level.
2. **Siege:** choose defense island/deck, offense deck/hero, and A/B/C target priorities; arm the monitor before Siege goes live. If the fight-start profile is still missing, capture one real Siege fight once when live.
3. **Upgrade:** capture one manual card upgrade with the 1.7.4 recorder. Do not repeat it endlessly: if Commander captures a mutation but cannot make it replayable, keep that sanitized local trace for inspection.
4. **Adventure:** press `REFRESH STATUS`, verify current/max energy and the campaign/island list, then farm one chosen stage.

Mail, choose-item rewards, and Challenge/Clash/Melee remain event-dependent tests and do not need to be forced when they are unavailable.
