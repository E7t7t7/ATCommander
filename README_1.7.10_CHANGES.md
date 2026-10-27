# AT Commander 1.7.10

## Swole fight/ticket repair

- Fixed Swole ticket discovery. Swole uses Challenge Energy inside the active Swole event (`challenge_data.energy`), not normal Adventure energy or Arena stamina. The live capture shows Anniversary Swole with event id `900042`, challenge id `1070`, and `8/8` Challenge Energy.
- Fixed the id mix-up in learned challenge-backed fight profiles. `event_id` and `challenge_id` are now substituted independently instead of treating both as the same value.
- Swole fight blocks now read live ticket count before every fight. If tickets are 0, the runner keeps the fight block queued and monitors until tickets refill instead of stopping/discarding the block.
- If tickets are available but the learned fight-start call still returns no battle, Commander reports it as a protocol/start failure instead of falsely calling it an energy problem.
- Added a persistent `Swole tickets: current / max` readout to the Swole tab.

## Siege

Siege 1.7.9 learning proved the browser sequence but direct replay still receives `Invalid combat data`. Siege is preserved as-is for the next live Siege window; no further speculative attack calls were added.

## Preserved working behavior

- Arena automation remains unchanged.
- Swole training schedule/monitoring remains unchanged except for fight-block handling.
- Adventure stage farming remains unchanged.
- Ad click calibration/replay remains unchanged.
