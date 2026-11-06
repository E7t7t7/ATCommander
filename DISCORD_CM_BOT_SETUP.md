# Bot DMs and orders without guild access (1.7.31)

Your own bot has a Discord profile and DM conversation. It does not inherit the
servers/channels your personal account can read. A channel URL does not grant a
bot access. If your guild cannot invite the bot, use the owner-DM route below.

1. Connect your bot in Commander. Invite it to a private server you control if
   needed, open its profile, and choose **Message** (or use **Open bot DM**).
2. Under Guild Siege orders, select **Use orders pasted into my bot DM** and
   Save guild sources. If switching from channel reading, reconnect. This mode
   does not request Message Content Intent and does not read guild channels.
3. Paste the entire Calculon roster into the bot DM as soon as you receive it.
   It matches your configured alias (e.g. DrMund0) or your actual Discord mention.
4. On Sunday paste the entire attack announcement into the same DM. The example
   “Going Family Guy and Art” becomes Family Guy first, then Art.
5. Save your Siege loadout/defaults and arm its Monday timer. Enable automatic
   defense setup if you want the assigned defense island applied during setup.

Plain-text DM commands, with optional `!` prefix:

- `help`, `status`, `siege`, `rumble`, `stop`
- `timer siege Monday 30`, `timer rumble daily 30`, `timer siege off`
- `command play-status` (same supported registry commands and PC-only restrictions)
- `cm Wingnut Amy`, `cmshop`
- `attack <pasted post>` or `defense <pasted roster>` if automatic recognition
  needs an explicit label. Paste text; forwarded snapshots/images are not parsed.

Only the configured owner can issue DM commands. Server chat cannot start phone
jobs. Orders are stored as parsed island names with an owner-supplied label and
receipt timestamp; raw posts/other members’ roster entries are discarded. The
owner is responsible for copying current posts: receipt time does not establish
the original post date or author. Attack orders expire after 72 hours; defense
assignments after seven days. Ambiguous imports do not replace valid orders.
Pasting orders does not arm a disarmed timer or start fights immediately.

Rumble automation’s deck remains configurable separately at **Discord & timers →
Saved defaults → Rumble**. Save its offense deck/hero and other settings there.
Siege and Rumble do not require or attempt ad watching before their event runs.

# Guild Siege sources and event defaults (1.7.30)

**Discord & timers → Saved defaults** now stores separate Siege and Rumble
loadouts: offense deck, hero/commander, defense deck, battle logic, and Siege
island/priority/fallback choices. Save each event’s defaults. They are independent
of manual Events selections and apply to timer and phone runs. Existing Events
settings are imported once as starting defaults. Before combat, Commander checks
the saved deck name/ID and verifies the selected offense deck and hero from a
fresh account read.

The two configured sources are read-only:

- Attack announcements: https://discord.com/channels/520905719516626945/1254172326413471834
- Calculon assignments: https://discord.com/channels/520905719516626945/559035124012941312

For channel reading, your bot must be a member of that server and have **View
Channel** and **Read Message History** on both sources (including thread access
where needed). A server administrator may need to grant this. Enable **Message
Content Intent** in the bot’s Developer Portal, enable guild reading in Commander,
save, and reconnect. Slash phone control alone still needs no content intent.
This uses your bot account, not your personal Discord login or Bender Bot.

Set your assignment alias (e.g. DrMund0) if it differs from your Discord mention.
Optionally restrict attack announcements to specific leader Discord user IDs.
Use **Fetch / preview guild orders** to see detected priorities and your assigned
island, with source timestamps. The example “Going Family Guy and Art” maps to
Family Guy first, Art second. The supplied Calculon example maps DrMund0 to Food.
Content and embed fields are supported; images/attachments are not interpreted.

Defense assignments may arrive ahead of Sunday: they are read independently and
remain eligible for up to seven days. Optional **Automatically apply my defense
assignment during setup** checks every five minutes while idle. It sets the island
only in the verified setup phase and confirms your membership afterward. A lost
or unconfirmed response is journaled and never resent blindly. There is also an
**Apply assigned defense now** button. These actions do not start battles.

Attack announcements remain eligible for 72 hours and are rechecked before due
Siege runs. Missing Sunday attack orders do not prevent early defense preparation.
Enable **Use fresh guild attack priorities** and/or **Use my Calculon assignment**
to bind those sources to Siege runs. Guild priorities map to the live enemy slots
before each fight; defense maps only to friendly slots. Unknown/conflicting/stale
orders wait for review rather than falling back to another week’s directions.
Attacking other islands after guild priorities fall requires its own checkbox.
Raw channel posts and other members’ assignments are not saved or sent to logs.

# Integrated Discord connection (Commander 1.7.29)

Open **Discord & timers** in Commander. This connection runs inside the app,
without an extra console or a separate CM-bot process.

1. At https://discord.com/developers/applications create an application and Bot.
2. Copy/reset its **bot token** and paste it into Commander’s masked Bot token
   field. It is saved in Windows Credential Manager; never paste it in chat.
3. In Discord enable **User Settings → Advanced → Developer Mode**. Right-click
   your own profile and choose **Copy User ID**. Put that ID in Commander.
4. Save connection, install Discord support if prompted, then **Connect**.
5. **Invite bot** opens an invite for your application with bot/application-command
   scopes. Invite it to your own server. Message Content Intent is not required.
6. From your phone use `/commander help`. Allow DMs from the bot for completion
   and failure alerts. Slash replies are private to the person invoking them.

Only the configured user ID can control this connection or use its CM commands.
Do not run the old standalone CM bot with the same token at the same time.
The integrated connection preserves `/cm` and `/cmshop`, with the existing live
shop-discovery limitations. Command registration may need Discord to refresh.

Phone commands:

- `/commander status` — active automation and timer state.
- `/commander run event:Siege` or `event:Rumble` — run with saved Events settings.
- `/commander timer event:Siege enabled:true minutes:30 day:Monday` — arm Monday checks.
- `/commander timer event:Rumble enabled:true minutes:30 day:Every day` — recurring checks.
- `/commander timer event:Siege enabled:false` — disarm this timer.
- `/commander command name:play-status` — run a registered supported command;
  named/JSON arguments use the same parser as Command Center. Purchases,
  destructive actions, outbound-message commands and arbitrary runner commands
  stay on the PC. Siege/Rumble combat uses the dedicated `/commander run` route.
- `/commander stop` — request Stop all and disarm event timers.

**Keep the PC awake and Commander open.** Discord does not host the game worker.
No personal Discord login token, self-bot or Bender messaging is used.

# AT Commander CM Discord Bot setup

The Discord bot is optional. The **CM Reviews** tab works without Discord.

## One-time Discord setup

1. Open the Discord Developer Portal and create an application.
2. Add a Bot to the application and copy/reset its bot token.
3. In the bot settings, enable **Message Content Intent** if you want `!cm` commands and free-form `@mention` requests. Slash commands `/cm` and `/cmshop` are the preferred path.
4. Generate an invite for the bot with the `bot` and `applications.commands` scopes, then invite it to your server. Normal Send Messages permission is sufficient for review replies.
5. Launch AT Commander once so its `.venv` exists.
6. In AT Commander, open **CM Reviews → START DISCORD BOT**, or run `Start_CM_Discord_Bot.bat`.
7. On first run, paste the Discord bot token into the console when prompted. Input is hidden. AT Commander stores it with Windows Credential Manager through `keyring`.

## Use

- `/cm Wingnut Amy`
- `/cmshop`
- `!cm Wingnut Amy`
- `!cmshop`
- `@AT Commander Wingnut Amy`
- `@AT Commander shop`

The Windows app and Discord bot use the exact same deterministic scoring engine. A review should not drift just because it was requested from a different interface.

## Runtime notes

- Keep `Start_CM_Discord_Bot.bat` running while you want the bot online.
- The bot refreshes live AT review data periodically and reuses a short cache between requests.
- It uses AT Commander's saved AT credentials; the Discord token and AT credentials are not bundled into project ZIPs.
- If the live CM shop payload cannot be mapped safely, `/cmshop` reports that limitation instead of inventing an offer. `/cm <name>` still works from current static/account data.
