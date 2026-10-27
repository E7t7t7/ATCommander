# AT Commander 1.7.18 changes

## New: CM Reviews tab

1.7.18 adds a dedicated **CM Reviews** tab. It is designed around the long-running community CM-review concept: rate the practical value of spending Combo Mastery resources *now*, rather than treating an old score as permanent.

The engine is deterministic and shared by the desktop app and Discord bot. It does not call an LLM for each review and does not randomly change its opinion between runs.

Current scoring inputs include:

- current AT static combo definition;
- current skill package and useful skill synergies;
- trait/BGE eligibility (traitless combos are penalized);
- current relative combo power signal from live skill scaling + attack/health multipliers;
- matching Power Card/final-form presence where exposed;
- detected recipe/result breadth across same-name static combo variants;
- trait/show-restricted skills where exposed;
- the account's current Combo Mastery level/tokens where AT exposes them;
- account recipe ownership when recipe input IDs are actually exposed;
- live offer currency/cost when Commander can identify the CM shop row safely.

The score is on a **0–10 half-point scale**. Practical recommendation bands follow the familiar CM-review decision framework:

- **9–10:** top-tier mastery target; deep CM can be justified, and a first 500-gem buy may be defensible.
- **7–8:** strong mastery-stone target.
- **5–6:** good/value CM1 target; deeper mastery depends on deck/account use.
- **3–4:** niche/account-dependent.
- **1–2:** poor resource value.
- **0:** pass.

Gem offers and mastery-stone offers are evaluated separately. Numeric `cost_type` values are deliberately **not** globally translated because AT reuses those codes in unrelated stores; the reviewer only labels currency when the CM payload or recognizable CM price gives enough evidence.

### Tab controls

- **REVIEW COMBO** — type a combo name and review it from current AT data.
- **REFRESH LIVE CM DATA** — refresh current card/mastery/shop/account data.
- **REVIEW LIVE SHOP** — review any priced CM rows Commander can safely map from the live payload. If AT changes/hides the shop shape, Commander says so instead of guessing.
- **START DISCORD BOT** — starts the optional local Discord bot.
- **BOT SETUP** — setup instructions.

Named combos with multiple static recipe/result rows are grouped together. Commander reviews the named CM purchase rather than whichever duplicate row happened to be found first. The detail panel reports the number of detected static variants as a conservative input-breadth signal; it does not pretend that number is an exact count of every current object in the shop pools.

## New: optional Discord CM bot

`Start_CM_Discord_Bot.bat` runs the same review engine through Discord. It installs the optional `discord.py` dependency into AT Commander's existing virtual environment the first time it is needed.

Supported interactions:

- `/cm <combo name>`
- `/cmshop`
- `!cm <combo name>`
- `!cmshop`
- mention the bot followed by a combo name, e.g. `@AT Commander Wingnut Amy`
- mention it with `shop` to request the currently detected live CM shop

The bot token is read from the `ATCOMMANDER_DISCORD_TOKEN` environment variable or stored in Windows Credential Manager through `keyring`. It is not written into the source tree/build archive.

The bot runs locally, so the PC hosting AT Commander needs to stay running for Discord requests to receive replies.

## Existing 1.7.17 behavior retained

No Arena/Rumble/Mail/Swole-training regression rewrite was done for this feature build. The 1.7.17 live Swole/Clash capture work, Nixon telemetry, Golden Pack pacing, and mouse-contained ad work remain in place for the next live tests.
