# AT Commander 1.7.30 — event defaults and guild Siege orders

Discord & timers now includes independent saved Siege/Rumble offense decks,
heroes/commanders, defense decks, battle logic, and Siege target/island/fallback
choices. Manual Events selections no longer determine timer/phone loadouts.
Initial defaults are copied from existing settings once. Saved deck names and
IDs are matched against fresh account state and offense switches are verified
before a charged fight. Renamed/missing/ambiguous defaults require review.

Optional read-only Discord sources are limited to the user-specified attack and
Calculon channels. The existing bot needs server membership, channel/history
permissions and Message Content Intent; private channel access cannot be granted
by Commander itself. See DISCORD_CM_BOT_SETUP.md. No personal Discord token or
self-bot is used. Source reading is off until configured.

The supplied Sunday attack example yields Family Guy → Art. The supplied Calculon
post yields Food Island for DrMund0 (or uses the configured owner’s actual mention).
Plain text and embed fields are parsed deterministically. Conditional, negative,
unknown, stale or conflicting directions are flagged for review. Preview includes
source timestamps. Optional leader IDs restrict human attack authors; defense
posts must come from Calculon’s bot account. Slot matching uses the current game
map, not assumed island positions. Guild fallback beyond priority targets is off
unless explicitly selected. Incoming messages are data, not arbitrary commands.

Defense and attack timing is independent: early defense assignments can be up to
seven days old; attack directions expire after 72 hours. Only the latest Calculon
message batch is considered. Background reads check every five minutes while
connected. Optional early defense setup (or its manual button) uses the verified
setup phase and reads back the player’s island membership. It does not start
combat. An uncertain change is recorded before sending and cannot be blindly
replayed. Stop all suspends guild setup work, and shutdown waits for its worker.
Missing attack posts do not block defense preparation. Due Siege runs wait for
required sources while Rumble may continue.

Existing Events layout, manual battle logic, normal automation and Discord phone
control are preserved. Live validation read the Siege response structure and
confirmed that own-island membership is visible. No battle or defense mutation
was used for testing; live Discord fetching requires the user’s bot permissions.
