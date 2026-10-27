# Local command reference

114 commands. Open Command Center, select a command, fill its arguments and choose Run Command. Required arguments have an asterisk. Save Result exports redacted output locally.

Resource-changing commands use fresh account state, explicit limits and stop on unknown outcomes. The Automation tab stores ordered routines. It does not run while Commander is closed. See BENDER_PARITY.md for verified behavior and remaining gaps.

Adventure `level` / `adventure_level` accepts chapter-stage, `progress`, `mastery`, or a current campaign ID followed by `-progress` / `-mastery`, such as `9040-progress`. Campaign examples are illustrative: preview the current real mission IDs first. Progress rechecks after each battle and stops when the exposed route is complete.

Arena `bypass_arena_turn_limit=True` removes Commander's 30-turn cutoff for Arena only. It is off by default, retains Stop/Pause, and still stops on an unverified move outcome. Best Daily explicitly keeps it off. Exact inactive challenge rejection `warning_126_text` is reported as a skip during Daily; other combat errors stop without replay.

Card commands accept `image_type=svg|html|png|jpeg`, `layout=grid|compact|list` and `columns=1..8`. Exports use selected exact-level statistics and available official public artwork. PNG/JPEG need Pillow; unavailable artwork is not substituted with invented images.

`guild-report` exports current public roster fields locally as dark searchable HTML, CSV or JSON. `guild-defense-plan` imports/exports CSV or JSON assignments; import defaults to preview, and `apply=True` saves local preferences only after exact member/guild validation. These commands do not publish a hosted spreadsheet or move another member's in-game defense.

Deck swap validates both originals and both replacements before the first write, then verifies each change from fresh state. Restore/swap create recovery backups and stop on conflicts or unconfirmed outcomes without automatic rollback.

## Adventure

| Command | Arguments | Purpose |
| --- | --- | --- |
| adventure-route | level=; mission (optional) | Preview a chapter-stage, story progress/mastery, or a scoped campaign route such as 9040-progress. |

## Arena

| Command | Arguments | Purpose |
| --- | --- | --- |
| arena-targets |  | Read the current Arena opponent, token hero and refresh price. |

## Automation

| Command | Arguments | Purpose |
| --- | --- | --- |
| daily-plan | options={} | Preview Best Daily against current quest requirements and resource limits. |
| daily-run | options={} | Run Best Daily with current quest requirements, explicit resource limits and availability checks. Requires spending review. |

## Buy

| Command | Arguments | Purpose |
| --- | --- | --- |
| buy-basic | quantity=1; budget=0 | Purchase basic packs with a fixed quantity or all affordable packs. Requires spending review. |
| buy-box |  | Read current box shop offers. |
| buy-converter | quantity=1 | Purchase converter packs with a fixed quantity or all affordable packs. Requires spending review. |
| buy-cost | type=cm; number=1; first_cost (optional); increment (optional) | Calculate standard consecutive CM or Mom's Box costs; offers may override these prices. |
| buy-deal |  | Read current deal shop offers. |
| buy-epicbasic | quantity=1 | Purchase epicbasic packs with a fixed quantity or all affordable packs. Requires spending review. |
| buy-herocrate | quantity=1 | Purchase herocrate packs with a fixed quantity or all affordable packs. Requires spending review. |
| buy-mastery |  | Read current mastery shop offers. |
| buy-max |  | Buy Basic Packs up to the live card inventory cap and available Nixons. Requires spending review. |
| buy-mombox | quantity=1; max_gems* | Buy Mom's Box pulls using the fresh price before every pull and an explicit gem budget. Requires spending review. |
| buy-momfree |  | Play the legitimately rewarded Mom's Box ad and verify the credited pull. |
| buy-rarebasic | quantity=1 | Purchase rarebasic packs with a fixed quantity or all affordable packs. Requires spending review. |
| buy-sep | quantity=1 | Purchase sep packs with a fixed quantity or all affordable packs. Requires spending review. |
| buy-turdpack | quantity=1 | Purchase turdpack packs with a fixed quantity or all affordable packs. Requires spending review. |

## Card

| Command | Arguments | Purpose |
| --- | --- | --- |
| card-combo | query=; character=; item=; trait=; skill=; skill2=; show=; min_rarity=1; display=full; image_type=none; layout=grid; columns=3 | Find current combo recipes by combo, character, item, trait, skill and input rarity. |
| card-info | query=; level (optional); display=full; image_type=none; layout=grid; columns=3 | Read a card or item \| character combo, select an exact static level, or export a local stat sheet. |

## Channel

| Command | Arguments | Purpose |
| --- | --- | --- |
| channel-create | name* | Create a named local guild chat/feed view. |
| channel-read | name* | Read a named local guild feed; created feeds poll for changes automatically. |
| channel-remove | name* | Remove a named local guild feed view. |

## Daily

| Command | Arguments | Purpose |
| --- | --- | --- |
| daily-upgrade | max_upgrades=3 | Finish only verified remaining Daily upgrades, protecting saved decks, foils, locked and Legendary/Mythic cards. Requires spending review. |

## Deck

| Command | Arguments | Purpose |
| --- | --- | --- |
| deck-build | query= | Search the owned card pool for deck construction. |
| deck-drop | name* | Remove a named local backup. Requires action review. |
| deck-hero | deck*; hero* | Set a saved deck's hero using an exact unlocked name or ID. |
| deck-list |  | List in-game decks and local named backups. |
| deck-map | deck*; display=full; image_type=none; layout=grid; columns=3 | Map a saved deck to owned cards or export exact current card levels. |
| deck-rename | deck*; name* | Rename an in-game saved deck. |
| deck-restore | deck*; name* | Restore a local named backup into a saved deck slot; all cards must still be owned. Requires action review. |
| deck-save | deck*; name= | Save a local deck backup; existing names are replaced. |
| deck-select | deck* | Switch the active deck to an existing saved slot. |
| deck-share | deck* | Create a shareable local deck list without account or card-instance identifiers. |
| deck-swap | deck*; other* | Swap two saved decks' cards and heroes, keeping local recovery backups. Requires action review. |

## Events

| Command | Arguments | Purpose |
| --- | --- | --- |
| events-all |  | List all events exposed by the current account. |
| events-current |  | List current events exposed by the current account. |
| events-upcoming |  | List upcoming events exposed by the current account. |

## Guild

| Command | Arguments | Purpose |
| --- | --- | --- |
| guild-accept | guild* | Accept a guild invitation by guild ID. |
| guild-activity |  | Read the current in-game guild activity feed. |
| guild-compare | guild* | Compare public information for your guild and another guild. |
| guild-configure | name (optional); defense_plan (optional); report_format (optional); columns (optional); assignments (optional) | Save per-guild local display settings, report columns and exact-member defense assignments. |
| guild-deck | guild (optional); member=; deck (optional); map=False; all_decks=False; max_profiles=50; display=full; image_type=none; layout=grid; columns=3 | List or map guild members' public active decks, with optional member/slot filters. |
| guild-defense-plan | mode=export; path=; format=csv; apply=False | Preview/import or export local CSV/JSON assignments; apply saves local preferences only. |
| guild-demote | player* | Demote a guild member by player ID. Requires action review. |
| guild-info | guild (optional) | Read public guild information by guild ID, or your current guild. |
| guild-inventory | member=; query=; min_rarity=1; type=all; trait=; show=; skill=; skill2=; fuse_level (optional); display=full; image_type=none; layout=grid; columns=3 | Filter private card data for this guild's current/local registered accounts. |
| guild-invite | player (optional) | List pending invitations, or invite a player by ID. |
| guild-kick | player* | Remove a guild member by player ID. Requires action review. |
| guild-leave |  | Leave the current in-game guild. Requires action review. |
| guild-mastery | member=; query= | Filter owned mastery data for this guild's current/local registered accounts. |
| guild-promote | player* | Promote a guild member by player ID. |
| guild-promoteleader | player* | Assign guild leadership to a member by player ID. Requires action review. |
| guild-reject | guild* | Reject a guild invitation by guild ID. |
| guild-report | format (optional) | Export a local dark searchable roster report or spreadsheet-compatible CSV/JSON. |
| guild-roster | guild (optional) | Read the current guild's roster and public metadata. |
| guild-search | name* | Search guilds by name. |
| guild-seen | guild (optional) | Read last-seen information exposed in the current guild roster. |
| guild-set | mode* | Change recruitment mode while preserving the live guild's other settings. Requires action review. |
| guild-top | max_profiles=50 | Read your guild's top 10 members by SFC rating. |
| guild-top-list |  | Read the game's current top SFC guilds. |

## Item

| Command | Arguments | Purpose |
| --- | --- | --- |
| item-choose | item=; quantity=1 | Resolve pending choices, or open selected consumables and choose Rogu → Characters → Items. Requires spending review. |
| item-list |  | List current named items, resources and quantities. |
| item-use | item*; quantity=1 | Use owned consumables; stone pulls consume 1,000 stones each. Choice items open one at a time. Requires spending review. |

## Play

| Command | Arguments | Purpose |
| --- | --- | --- |
| play | mode=arena; count=1; reserve=0; deck (optional); mission (optional); adventure_level=; event (optional); min_energy=0; max_level (optional); swole_level (optional); skip=False; bypass_arena_turn_limit=False; melee_character_first=False; collect_research=False; claim_daily=False; hero_tokens=; max_refreshes=0; max_refresh_nixons=0; collect_quests=False; use_lootcrate=False; buy_basic=False; takeover_quest_mode=; claim_mega=False; claim_swole=False; claim_clash=False; claim_ecmc=False; claim_sfc=False | Run a bounded serial battle block with the established Item → Combo engine. Melee optionally uses character first, with game-AI skip off. Adventure accepts chapter-stage and scoped progress/mastery routes. Requires spending review. |
| play-defaults | mode=list; options={} | List, set or clear persistent local play defaults. |
| play-skip |  | Skip the remaining plays of a current battle through the game's own skip operation. Requires action review. |
| play-status |  | Read live Arena/Adventure energy and active Challenge/Swole/Clash/Melee events. |

## Recycle

| Command | Arguments | Purpose |
| --- | --- | --- |
| recycle | common=True; rare=True; epic=False; execute=False | Preview or recycle conservative level-one loose Common/Rare/Epic cards. Requires action review. |

## Register

| Command | Arguments | Purpose |
| --- | --- | --- |
| register | alias*; user_id*; login_hash* | Verify and save a local account alias in Windows Credential Manager. Requires action review. |
| register-guild | alias*; user_id*; login_hash*; role=officer | Save a verified local guild officer profile. Requires action review. |

## Research

| Command | Arguments | Purpose |
| --- | --- | --- |
| research-ad |  | Play a legitimate research ad and verify its task or speedup benefit. |
| research-collect |  | Collect completed combo research only. |
| research-deck | deck*; max_pairs=3; speedups=False; max_hours=0 | Research missing pairs in a saved deck. Optional one-hour speedups have an explicit total budget. Requires spending review. |
| research-info |  | Read current research slots, tasks and completion times. |
| research-missing | deck* | List current unresearched recipe pairs in a saved deck. |
| research-plan | options={}; scope=user | Preview real unresearched owned input pairs without starting research. |
| research-reconcile | scope=user | Check the saved uncertain action from fresh game state without spending resources or arming Research. |
| research-run | options={}; scope=user; resume=True | Advance a saved queue by up to three verified actions; stop and resume between requests. Requires spending review. |
| research-speedup | item*; slot*; number=1 | Use a selected research speedup item in a chosen slot. Requires spending review. |
| research-start | card1*; card2*; slot* | Start a specific combo in an available research slot. |

For the owned-combo queue, `options={"speedups":true,"use_all_speedups":true,"reserve_hours":0,"max_pairs":100000}` spends available speedups on matching unfinished recipes without the numeric hour cap. The desktop checkbox **Use all available speedups (no reserve)** sets this mode. Use `resume=False` / **NEW QUEUE** to apply changed settings; a pending request is checked before the old queue is replaced. `research-reconcile` / **CHECK SAVED ACTION** checks recovery independently of edited settings and does not replay the saved request.

## Reward

| Command | Arguments | Purpose |
| --- | --- | --- |
| reward-claim | kind=all; execute=False | Preview or claim selected daily, completed quest and named event milestones using live IDs/cycles. |

## Rewards

| Command | Arguments | Purpose |
| --- | --- | --- |
| daily-login |  | Claim explicitly available free login rewards and verify fresh calendar counters. |

## Rumble

| Command | Arguments | Purpose |
| --- | --- | --- |
| rumble-deck | deck*; event*; cycle* | Set a Rumble defense deck for a current event/cycle. |
| rumble-match |  | Read current Rumble match state and remaining participation. |
| rumble-opp |  | Read current Rumble opponent and match state. |
| rumble-rank | event* | Read guild rankings for a selected live Rumble event ID. |
| rumble-results |  | Read Rumble match history exposed by the server. |
| rumble-stats |  | Read Rumble member/match rankings. |

## Shop

| Command | Arguments | Purpose |
| --- | --- | --- |
| shop-purchase | store_id*; quantity=1; budget*; expected_currency_item (optional) | Purchase a live box, deal or CM offer by store ID. Re-read its price before each real single purchase. Requires spending review. |

## Siege

| Command | Arguments | Purpose |
| --- | --- | --- |
| siege-deck | deck* | Set your Siege defense deck. |
| siege-defense | expected= | Compare live Siege defense assignments with a local JSON plan. |
| siege-history |  | Read recent Siege history. |
| siege-islands |  | Read live Siege islands and defensive state. |
| siege-join |  | Join the available Siege event with your current guild. |
| siege-match |  | Read the current Siege phase, opponent and attack availability. |
| siege-move | slot* | Move your defense to a live Siege island slot. |
| siege-opp |  | Read the live Siege opponent and islands. |
| siege-rank |  | Read the current Siege rankings. |

## Subscribe

| Command | Arguments | Purpose |
| --- | --- | --- |
| subscribe | type*; interval=300 | Monitor game changes locally while Commander is open. |

## Takeover

| Command | Arguments | Purpose |
| --- | --- | --- |
| takeover-status |  | Preview current Takeover quest progress and requirement availability without switching heroes or fighting. |

## Unregister

| Command | Arguments | Purpose |
| --- | --- | --- |
| unregister | alias* | Remove a local account alias, or all local aliases. Requires action review. |

## Unsubscribe

| Command | Arguments | Purpose |
| --- | --- | --- |
| unsubscribe | type* | Remove a local game notification subscription. |

## User

| Command | Arguments | Purpose |
| --- | --- | --- |
| user-account | alias= | List or activate a saved local account alias. Requires action review. |
| user-ad | mode=watch | Watch legitimate rewarded ads or inspect the current boost. |
| user-claim | type=daily; event (optional) | Claim daily login, event rewards, event milestones, or a Mega Quest milestone. |
| user-heroes |  | Read unlocked heroes, training levels and progress. |
| user-inventory | query=; min_rarity=1; type=all; trait=; show=; skill=; skill2=; fuse_level (optional); display=full; image_type=none; layout=grid; columns=3 | Filter and export your owned card inventory locally. |
| user-mail | mode=read | Read, claim, or delete safe mail. Unclaimed reward mail is always protected. |
| user-mastery | query= | Read/filter your owned Combo Mastery levels and loose token counts. |
| user-profile | player* | Read a player's public game profile by their player ID. |
| user-quest |  | Read current achievements, daily quests and milestone progress. |
| user-rank |  | Read the current global SFC leaderboard. |
| user-squad | mode=status; hero (optional) | View Swole training, start an unlocked hero, or collect completed training. |
