# AT Commander 1.7.31 — bot DMs, pasted orders and event ad independence

The integrated bot now accepts plain owner-only DMs like `status`, `siege`,
`rumble`, `stop`, `timer siege Monday 30`, `command play-status`, `cm <name>` and
`cmshop`. Optional ! prefixes are accepted. Guild-channel messages and other
users cannot trigger these actions. Existing slash commands remain available.
The desktop page adds Open bot DM; Discord may require a shared private server
before the owner can open a conversation with the bot.

A DM-only guild-orders option works when the bot cannot join the guild server.
The owner can paste a full Calculon roster and later the Sunday attack post,
without a command prefix. Explicit attack/defense prefixes are also supported.
Only parsed priorities and the owner’s assignment are retained, labeled as
owner-supplied with receipt time. Raw posts and other roster entries are not saved.
The existing freshness windows, live island matching, default loadouts, early
defense setup and Monday timer apply. Imports do not arm timers or start fights. A defense-only import waits for the Sunday attack post before scheduled fights; early defense setup remains available.
Ambiguous text is rejected before replacing prior valid directions. Channel
reading is disabled in this mode, so missing guild permissions cannot erase DM
orders. No personal Discord login token or inherited user access is assumed.
See DISCORD_CM_BOT_SETUP.md for one-time configuration and phone commands.

Siege and Rumble EventBattleRunner paths now skip ad_preflight entirely,
regardless of the Auto-watch ads setting. An inactive boost cannot block these
manual, timer or phone event runs, and these paths do not open an ad window.
Other automation keeps its existing ad behavior and worker exclusivity.
Rumble’s independent saved automation deck/hero defaults remain available.

1,431 checks pass, covering owner-only DM dispatch, pasted post recognition,
no raw roster persistence, stable DM orders without guild reads, and both event
modes completing their mocked starts without calling ad preflight. No live
battle, Discord message or ad completion was sent during verification.
