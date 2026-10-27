# AT Commander 1.7.5 — fixed ad viewport / Siege battle-state recovery / dynamic Adventure islands

## Ads

The 1.7.4 ad-calibration concept was correct but the browser geometry was not stable enough. 1.7.5 forces the reused AT Edge window to a fixed 1280x900 outer size and fixed position every time the ad workflow runs. The page is returned to scroll position 0,0 and wheel/touch scrolling is blocked during calibration/playback.

`Play Now` is no longer reached by scrolling. Commander first searches buttons/links/role=button controls, including open shadow roots, then falls back to Chromium's accessibility tree. Either method clicks the control without moving the viewport. If the game canvas already exists, Commander does not click anything.

Ad calibration therefore happens in the same no-scroll geometry that WATCH ADS later replays. The legitimate flow remains browser-driven and every completed ad is still verified through `getBoostLevel`; no fabricated ad-completion request is sent.

## Siege

The latest live test proved A/B/C target selection is working: Commander selected the configured Bob's Burgers island correctly. The remaining failure was the learned fight-start response not exposing top-level `battle_data`.

1.7.5 makes battle-state extraction tolerant of wrapped event responses and direct battle objects. If a Siege start call only acknowledges the attack, Commander immediately checks fresh Siege status for the newly-created active battle before giving up. When no battle can be recovered, the log now includes the exact learned message plus response keys/result/error so the remaining protocol problem is diagnosable without guessing.

Siege target-parameter substitution is also broader for target/enemy/defender/location/siege ID fields, which helps older learned profiles whose target parameter was not literally named `slot_id`.

## Adventure event-island detection

The working stage-farm engine is unchanged.

Adventure discovery now treats `current_campaign_<event>` values as possible mission IDs (as observed in live player prefs), resolves them through `mission_data` to their owning campaign, and groups mission data by campaign even if a corresponding `campaign_data` row is temporarily absent. This lets a newly-added/random fourth event island surface after Refresh Account without requiring a hard-coded island name.

Active event rows are also inspected generically for campaign/mission metadata rather than relying only on one event type value. Active islands sort before inactive/static islands in the selector.

## Intentionally unchanged

Arena automation and the working Swole training scheduler/concurrent monitoring were not modified.
