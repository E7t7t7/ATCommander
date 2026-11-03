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
