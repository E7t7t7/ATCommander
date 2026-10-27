# AT Commander 1.7.12

## Siege: no more stale replayXHR request IDs

The prior Siege path could save Chromium's `requestId` from a successful manual `fightGuildSiege`, but that ID is target-local and becomes stale. 1.7.12 adds a browser-context learned-flow executor: it rebuilds the already-captured action fields with the current live target values and executes the sequence inside the running AT game target. Current credentials are injected only in memory and are never written into protocol profiles or handoff files.

When Siege is armed, Commander now best-effort opens/prepares the fixed game window. At attack time it tries the browser-context learned flow. If the browser is not ready, Commander leaves the attack unspent and retries on the next poll. It does **not** fall through to the direct `requests.Session` reconstruction already proven to return `Invalid combat data`.

This is a structural fix and cannot be declared live-proven until the next weekly Siege.

## Adventure event-island mission discovery

AT has used versioned/hashed static resource URLs that do not always end in `.json` or include `/data/`. Resource discovery now keeps any live mission/campaign URL whose name matches the requested catalog, then verifies the payload instead of rejecting the URL by filename shape.

If a discovered CDN/static URL fails through the normal Python session, Commander now asks the live game browser to fetch and parse that resource. This is intended to recover temporary/event mission maps such as Anniversary without making the user type raw mission IDs. Main Adventure behavior is unchanged.

## Delete Mail: dynamic IDs and reward protection

The learner now parameterizes `mail_id` / `mail_type` for Delete Mail instead of freezing the one message used during capture. After one real delete mutation has been learned, **DELETE SAFE MAIL** enumerates the current mailbox and deletes only:

- already-claimed reward mail, or
- mail with no reward.

Unclaimed reward-bearing mail is preserved automatically. Progress is reported as `Delete mail X/Y`. The delete mutation itself is still capture-driven; no endpoint name was guessed.

## Item-choice priority prepared without blind auto-selection

Choice responses now retain explicit AT type/rarity/value hints when present. The Resolve Item Choice dialog sorts/recommends:

1. Rogu
2. explicitly identified Characters
3. explicitly identified Items
4. unknown types in AT's original order

Within a known tier, higher explicit rarity/value wins. Commander still requires the user to press **SELECT REWARD** and still requires the real learned item-choice mutation; 1.7.12 does not blindly auto-select an unproven response.

## Preserved from 1.7.11

- Swole uses the live `challenge_data.level` before every fight.
- Challenge/Clash/Melee refreshes live challenge level/energy before each fight.
- Claim All Mail uses current live mail IDs.
- Non-choice item/resource spending uses bounded parallel one-pull requests.
- Nixon purchasing reuses worker sessions, supports up to 12 workers, and reports readable progress.
- Arena, Rumble, Swole training, normal Adventure farming, and the proven Item → Combo battle order are unchanged.

## Still open / intentionally not claimed fixed

- Siege browser-context start must be validated during the next live Siege.
- Daily Upgrade still has no verified AT mutation request.
- Item-choice automatic execution remains gated on a proven learned selection endpoint.
