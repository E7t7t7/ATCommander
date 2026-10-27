# AT Commander 1.7.6 — Play Now teaching / Siege start repair / event-stage labels

## Ads
- Added **TEACH PLAY NOW**: one manual click in the fixed/no-scroll browser saves a viewport-relative launcher coordinate. WATCH ADS uses it when DOM and accessibility-tree clicks cannot reach Kongregate's Play Now control.
- Added **TEST AD CLICKS**: replays one saved ad click cycle even when the account is already at boost level 3. This is a mechanical browser test; AT may not offer another ad while maxed.
- The existing legitimate-ad boundary remains: Commander replays browser UI actions and verifies state with `getBoostLevel`; it does not fabricate ad-completion requests.

## Siege
- A/B/C priority selection remains unchanged.
- `fightGuildSiege` no longer depends only on a learned profile that may contain no target parameters.
- When the learned start request fails, Commander tries conservative target payload shapes built from the live Siege status: slot, location, event, enemy faction, and (when needed) a defender from the selected island.
- The first working parameter shape is cached for the rest of that run and rematerialized with the current target, so changing from A to B/C does not reuse stale IDs.
- Failure logs now include `result_message` for each attempted request shape. If the server consumes an attack without returning recoverable battle state, Commander stops retries to avoid burning another attack.

## Adventure event islands
- `current_campaign_*` markers are now interpreted as either a current mission ID **or a campaign ID**. This matters for event islands such as Anniversary, where the live marker can identify the campaign directly.
- Event-island dropdowns display human stage labels instead of forcing the user to work with raw mission IDs. The mission ID is kept internally for `startMission`.
- Temporary/new event campaigns still surface dynamically from live campaign/mission metadata.

## Preserved
- Arena battle engine unchanged.
- Swole training scheduler/concurrency unchanged.
- Main Adventure farming unchanged.
