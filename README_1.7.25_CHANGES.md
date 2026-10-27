# AT Commander 1.7.25

This release extends the current working 1.7.24 folder. The original Arena, Rumble, Adventure, Swole training, mail and Nixon buying paths remain in place. The direct combat signing, live CM shop, event Adventure map and reward-choice fixes from 1.7.24 are retained.

## Desktop and startup

- Native `AT Commander.exe` GUI launcher. Python environment creation and dependency setup use hidden child processes with a dark setup window. Warm startup opens only Commander. Python 3.10+ remains a prerequisite.
- Dark sidebar navigation with separate battle modes, Inventory/Mail, CM Reviews, Command Center, Automation and Tools/Ads.
- Arena start/pause/stop controls have their own row and remain visible at the supported window size.
- The compatibility VBS and BAT entry points delegate to the EXE.

## Local command layer

- Handlers for all 93 observed Bender command names, with typed arguments, bounded quantities, live resource budgets and redacted results. `BENDER_PARITY.md` distinguishes local replacements, server gates and missing option parity.
- Local named deck backups, restore/swap verification, card inventory filtering/export, current combo recipes and two-input lookup, quests, research, event data, public guild deck maps and rankings. Show and two-skill filters accept human skill names.
- Multi-account aliases store new credentials in Windows Credential Manager. Profile metadata, preferences and deck backups contain no login secrets.
- Serial saved routines, repeating schedules, local notifications and named guild feeds. Repeats wait for active battle runners; routines start disarmed on launch and stop on an error.
- Bounded `play` uses the established battle policy or the game's explicit skip operation, with energy reserves, maximum challenge levels, Swole farming levels, story chapter/progress/mastery routes, Arena token targeting and total refresh budgets, research collection and daily claim options. Read-only route/token previews are available.
- Long command forms scroll while Run/Add to Routine and results remain visible. New account-tool reads are paced and reuse account/profile snapshots; existing battle and purchase runners retain their timings.
- Choice items open serially, confirming each Rogu → Characters → Items/Power Cards selection before the next pull. Refill aliases resolve the current inventory's real names.
- Named play switches claim Mega Quest, Swole, Clash/Melee, ECMC and SFC milestones after the battle block, plus completed quest collection. `reward-claim` previews the actual live event/cycle IDs before execution. Optional three Basic Packs and VIP Loot Crates are bounded by current balances; Loot Crates require explicit current VIP status. Ad-locked crates retain their legitimate playback requirement.
- Golden Pack cancellation preserves the confirmed count and continues to use actual single purchases. Uncertain purchase/action outcomes are never replayed.
- Daily Upgrade is available as a bounded command as well as the existing Inventory tool, with the same protected-card and live-quest checks.
- Research ads and Mom's Box ads have separate legitimate-playback calibrations and server-benefit checks. No fabricated watched-ad completion is sent.

## CM community lookup

Public RSS is preferred where anonymous JSON is rejected. A shared recent feed reduces per-combo searches. Requests are serialized and paced; provider cooldowns are respected. Combo section boundaries prevent neighboring review scores being borrowed. Explicit dated ratings are deduplicated and use the latest rating per reviewer. Public evidence survives app restarts for up to seven days as clearly marked cached/stale data. Stale evidence and large disagreements do not replace the current card-data score.

Cached rating ages and aggregate scores are recalculated from their evidence on restart. Shop status now distinguishes cached evidence and unavailable lookups from a valid missing-rating result.

## Validation on September 30, 2026

- Local regression and command contract/safety tests pass; the exact final count is recorded in `BENDER_PARITY.md`.
- The EXE's self-test verifies GUI subsystem, hidden child processes, Python discovery and the existing runtime marker.
- Live read validation passed card lookup, 249 Wingnut Amy recipes, 25 decks, deck mapping, 161 inventory entries, mastery, quests, 38 current event rows, guild information, research state/missing pairs and energy/event status.
- All five initially rejected reads passed after a quiet interval/slower pacing: guild activity, guild SFC profile ranking, Rumble history, Rumble member statistics and Siege rankings. Focused checks also verified 123 owned mastery entries and every card name in a 25-card guild deck map.
- Public Reddit lookup found dated Wingnut Amy evidence. The immediate Buckarito request was rate limited. Lookup pacing was then added; provider errors remain distinct from a valid no-match response.
- The exact current 25-card deck and hero were successfully restored and verified without changing the selection. A swap between different slots and paid/consuming command variants still require controlled live verification.
- Story chapter `23-1`, progress and fully completed mastery previews passed. Current Arena token reward/refresh price, two pantry-guns/Peter recipes and 13 show-filtered owned Rogu cards passed live reads.
- The attempted level-one Swole skip check found zero tickets and sent no battle start. Skip remains awaiting energy for live verification.
- No battles, packs, stones, gems, guild membership changes or card upgrades were consumed during this continuation's read validation. Existing confirmed-working combat was not repeatedly retested.

Siege combat remains dependent on an available attack phase. Golden Pack throughput still needs a live run with sufficient Golden Turds. Mom/research ad calibration and playback require a responsive game browser and a real available ad; browser control timed out during the continuation's inspection.

## October 1 continuation

- The fresh release EXE opened the dark app and connected successfully; the crash log was empty.
- The reward preview mapped nine real daily/Mega Quest/event claim requests, including the SFC cycle, without sending any claims. Current non-VIP status was verified. No items or game resources were spent.
- The regression suite passes 214 tests. The ZIP now includes the public synthetic signing fixture required to run its packaged tests.
- A quiet boost read verified level 1. Ad credit checks now wait the full configured window with reads at least eight seconds apart; unreadable baseline state prevents playback, and partial verified boost stays visible. These fixes pass timing/status fixtures; complete live playback is still pending.
- Tools setup text no longer asks for new captures for built-in actions or existing ready profiles. Ad calibration remains separate. Inspecting the dedicated Edge ad window was blocked by a desktop-tool approval timeout, so no new ad playback was attempted.
- The game browser still timed out while binding the existing tab. Rewarded ad verification remains blocked on responsive browser control. Takeover hero cycling, PvP Challenge token hunting and the remaining advanced card presentation options are still unfinished.
