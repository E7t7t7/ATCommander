# AT Commander 1.7.3 — recovery patch

This patch is based on the user's 1.7.2 live tests and the new Adventure/Swole captures.

## Capture / Diagnostics
- Fixed the "No useful AT API calls" failure caused by Chrome DevTools sometimes exposing an empty request POST while AT echoes the real action parameters in the JSON response under `body.request`.
- Learning now correlates request/response by request ID and recovers those echoed action fields.
- Upgrade, Swole battle, Challenge/Clash/Melee and choice-item learning use stricter action fingerprints so a nearby background mutation is not auto-linked by mistake.
- Confirmed built-in calls are labeled `BUILT IN` instead of asking the user to relearn them.
- Trace sanitizing now also redacts Kong/session/token/signature-style fields before saving captures.

## Adventure
- Arena, Adventure and Swole have independent runner objects and can run concurrently.
- Adventure now shows live current/max Adventure energy from `user_data.energy` / `max_energy`.
- Main Adventure remains Island -> Stage (for example 3-1 -> mission 107).
- Player campaign markers and any resolvable campaign/mission static metadata are exposed as additional campaign/island choices rather than flattened into hundreds of meaningless mission IDs.
- Explicit stage farming remains available even when per-stage completion/star data is not exposed. Auto-detect-incomplete still refuses to guess until a real completion/progress shape is captured.

## Challenge / Clash / Melee events
- Live type-6 events are discovered from `active_events` / `upcoming_events` (excluding Swole, which stays on its own tab).
- The selector shows current challenge energy where AT exposes it.
- Separate offense deck, offense hero and Item->Combo tiebreak logic are available.
- One clean fight-start capture is still required for the generic event start call; the repaired learner is intended to make that capture work.

## Swole
- Status combines `getHeroSquad`, `getUserAccount`, `init`, and fresh training mutation responses so an active training timer is not falsely reported as idle.
- Training schedules can execute even before the fight-start call is learned. If a FIGHT step is reached without that call, the queue pauses and is preserved instead of being discarded.
- Swole fight energy is shown when present in the live Swole event payload.
- `startHeroTraining` / `completeHeroTraining` remain the confirmed training calls.
- A fresh Swole fight capture is still required before fight blocks can execute.

## Inventory / spending
- Unresolved `Unknown item (ID ...)`, `Item ####`, and `Card ####` rows are suppressed from normal Account Snapshot / Stats views. They remain available through the Inventory "Show unresolved IDs" diagnostic toggle.
- Nixon buying can use multiple controlled workers (1-6, default 4) to avoid the painfully slow one-request-at-a-time UI loop. Parallel=1 remains the safe fallback if AT rate-limits requests.
- Item spending distinguishes **number of pulls/uses** from **resource-unit cost**. For known 1,000-unit stone-style pulls, the dialog shows balance, cost per pull, max pulls, and total spend before confirmation.
- Choice-style items are opened only one pull at a time. If AT exposes named reward options, they are cached for a `RESOLVE ITEM CHOICE` action after the choice-selection protocol is learned.

## Events / existing battle logic
- Arena's validated Item -> immediate Combo sequence is unchanged.
- Rumble/Siege offense deck + offense hero controls remain separate from defense configuration.
- Closing the app now stops Arena, Event, Adventure and Swole runners.
