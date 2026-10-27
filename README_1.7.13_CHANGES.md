# AT Commander 1.7.13

## Live results this build responds to
- Claim/Delete Mail: confirmed working; no rewrite.
- Swole: live challenge 1070 at level 87 still returned `Invalid combat data / warning_157_text` with tickets available.
- Hangover Clash: live challenge 5143 at level 20 returned the same rejection.
- Nixon: large 500-pack purchases were still painfully slow.
- Golden Turds: runs above roughly six concurrent pulls were unreliable.
- Adventure: Main was usable, but temporary/event islands were not practically selectable.

## Battle-start change
The same rejection on Swole and Clash proves the remaining problem is not just stale challenge IDs or levels. Commander now detects the authoritative invalid-combat response, opens/reuses the fixed logged-in game browser, and retries the same learned `startChallenge` request in that live browser context. Once a runner sees this rejection, it stays on browser-context starts for the rest of that run.

No new endpoint was invented. Challenge ID and level still come from live event data before each fight.

## Nixon fast batching
Large pack purchases no longer force one request per pack. Each worker sends multi-pack quantities (up to 50 per call). If AT explicitly rejects a batch size, Commander recursively splits only that rejected batch until AT accepts it. Network/unknown failures are not blindly retried, avoiding accidental duplicate purchases after a lost response. Parallel setting remains 1-12 and now persists up to 12 instead of being silently clamped back to 6 on save.

## Golden Turd stability
Golden Turd ID 200005 now uses strict waves of at most six one-pull requests. Commander waits for the entire wave to complete, pauses briefly, then starts the next wave. This prevents the previous continuous-worker overlap where the next pulls could begin before the prior group fully settled.

## Adventure event islands
When the static mission/campaign catalogs are available, they remain authoritative. If they are missing, live `current_campaign_<event>` player-pref markers are now surfaced as `Live Event` choices with the current marker value as a selectable current mission. Anniversary's observed marker was 9040. Event selection is also preserved across Refresh instead of falling back to Main just because the rendered label changed.

## Intentionally unchanged
Arena, Rumble, normal Adventure Main farming, Swole training scheduling, Claim Mail, Delete Mail, Siege live behavior, and the universal Item → immediate Combo battle ordering.
