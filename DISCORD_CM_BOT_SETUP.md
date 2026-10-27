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
