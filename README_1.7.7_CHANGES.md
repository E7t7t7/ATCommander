# AT Commander 1.7.7 changes

## Siege: stop guessing combat payloads

1.7.6 proved the A → B → C island-priority selector works, but AT rejected guessed `fightGuildSiege` payloads with `Invalid combat data.`. 1.7.7 no longer sprays guessed target-field combinations at the live attack endpoint.

- Added **LEARN ONE SIEGE ATTACK** directly on the Siege panel.
- While Siege is live, arm the learner, manually start exactly one successful Siege fight in the persistent Learning Browser, then finish the learner.
- The learner now strongly prefers `fightGuildSiege` and especially requests containing `combat_data`.
- Captured Siege `combat_data` is preserved in its exact shape while target-specific IDs are templated for the live A/B/C island.
- Learned-profile placeholder substitution now works recursively inside nested dictionaries/lists, not only flat POST fields.
- If a learned Siege profile is rejected, Commander will not retry the same profile every polling cycle and risk wasting attacks.

## Ads: native Windows input

Chrome/Edge CDP synthetic canvas clicks were recorded correctly but Unity ignored them during replay on the user's machine. 1.7.7 therefore replays calibrated clicks as real Windows mouse input when running on Windows.

- Fixed/no-scroll browser geometry is retained.
- Fresh Play Now and ad calibration records exact screen coordinates as well as normalized canvas coordinates.
- Replay foregrounds the dedicated Edge/Kongregate window and sends a real Windows left click.
- CDP remains a fallback if native input is unavailable.
- Existing safety boundary remains: Commander does **not** fabricate or directly submit ad-completion credit. AT must verify the boost through `getBoostLevel`.

Because exact screen coordinates are new, **teach Play Now and calibrate one ad again in 1.7.7** before evaluating replay.

## Adventure event-island stage data

AT's current `getStaticFiles` response exposes card files but omits mission/campaign files even though `init` still supplies `mission_data_version` and `campaign_data_version` hashes.

1.7.7 now uses those live version hashes to try the AT CDN's versioned mission/campaign catalogs. When available, temporary/event islands such as Anniversary can be grouped into human-readable Stage 1 / Stage 2 / ... choices while keeping raw mission IDs internal.

Main Adventure farming is unchanged.

## Kept intact

- Arena API battle engine and hard Item → immediate Combo sequence.
- Working Swole training scheduler/monitoring and Arena+Swole concurrency.
- Rumble automation.
- Siege A/B/C priority selection and fallback behavior.
