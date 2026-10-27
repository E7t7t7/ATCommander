# AT Commander 1.7.24

Continues from the extracted 1.7.23 source after reading the README and every existing version change log. No older-version rebuild was used.

## Direct signed combat

- Reconstructed the current public Unity client's message and client signature generation. Both matched a real logged-in client's startChallenge request in a private, local equality check; no signature values were logged.
- Every start gets fresh integer Unix time, newly computed signatures and current client/version fields. Saved timestamps/signatures are replaced.
- Swole, Challenge, Clash and Melee use direct Python HTTP. Their normal screens and runners no longer require a learned click, request ID, capture profile, manual initialization or mini signing browser.
- Fresh active-event data supplies the inner challenge ID, level and energy. Rejected, missing, upcoming or ended state cannot start a fight. An ambiguous start stops safely instead of replaying a potentially spent ticket.
- The Swole training schedule and existing combat policies remain in place.
- The public Siege client sends fightGuildSiege with only the enemy slot_id. The monitor now uses that signed shape and starts only in phase 2 (Battle), with a current living enemy island and available attacks. Opt-in, matchmaking, scoring, ended and unknown phases cannot attack.
- Signing defaults match the live public WebGL 1.181.0 client. A future game-client update may require updating the version fields or signing implementation.

## Catalogs, shop and rewards

- Read embedded init catalogs before trying static-file URLs. Validate each catalog's row IDs and reject configuration/error JSON instead of treating it as a catalog.
- Publish authoritative deck and unlocked-hero choices before optional catalog downloads, so Arena, Events and Swole selectors populate promptly. Later name enrichment preserves the selected IDs and does not clear inventory while loading.
- Reuse the existing init snapshot for static catalog version hashes instead of repeating init for each candidate lookup. All existing CDN candidates and fallback behavior remain available when no snapshot exists.
- Temporary Adventure chapters use their actual catalog mission IDs. Campaign marker 9040 is not a mission ID. Same-named chapters are selected by stable campaign identity, including when ACTIVE labels change.
- Discover current CM-shop offers from MasteryTokenPack store entries and their token rewards. Explicit REVIEW LIVE SHOP refreshes the offers; failed refresh clears stale offers. Background community checks cannot replace another review view.
- Preserve the corrected current-power CM scoring and use the same engine in the Discord companion.
- Golden single buys use a persistent session and start-to-start adaptive pacing. Request time counts toward the interval. Explicit rate-limit rejections may retry after cooldown; ambiguous network/server outcomes stop with the correct partial count.
- Restore already-pending choice rewards during account refresh. Automatic choice uses Rogu, then other Characters, then Items / Power Cards; within a tier it uses rarity, current catalog power and health. Keep the real option selector distinct from the reward's card ID and verify removal after confirmation.
- Daily Upgrade uses upgradeUnit and requires explicit live quest progress. Protect every saved-deck card, locked cards, foils and high-rarity cards; verify both the upgrade and quest increment after each action. An absent quest causes no upgrades.

## Ads and privacy

- Rewarded-ad calibration/replay preserves the full taught wait, up to five minutes; the previous twenty-second truncation is removed. Existing sequences that already lost their timing need a fresh legitimate calibration.
- No raw ad-credit request is used to represent an unwatched ad. The user's existing browser adblock extension is preserved.
- Mask the entire saved login hash. Recursively redact encoded JSON credentials/signatures/session data and suppress private request URL causes and debugger-session URLs in diagnostics.
- Runtime credentials, browser profiles, captured sessions, settings and downloaded reverse-engineering artifacts are excluded from the release ZIP.

## Validation on the authorized main account

- A zero-energy interception captured a real signed Fetch request and blocked it before transmission.
- Direct signed Swole start plus existing combat engine completed one battle. The actual one-fight schedule completed a second battle without a capture/helper. Both were losses at level 92; two tickets were spent, leaving one at the final check.
- Current event Adventure mission 9258, catalog campaign 9040, started and completed through the existing engine in seven moves. It was a loss and consumed seven Adventure energy.
- Live CM discovery returned four offers: Video Call Joseph, Guenter, Explosive Lana and Roger in Disguise, with their live currency/cost data.
- The restarted native UI populated deck and hero selectors. Offline tests verify that this selector event precedes catalog reads, later name enrichment preserves selected IDs, and static version lookup does not repeat init when a snapshot exists.
- One Legendary Power Stone pull exposed three PCs. The parser safely paused while their classification was fixed; the same pending choice then selected Chokehold Cheryl and the server confirmed zero pending choices. No second pull was opened.
- Golden live speed was not measured: the account held 619 Golden Turds, below the cost of one pack. No Golden purchases were made in this continuation.
- Clash/Melee/normal Challenge energy was zero, Siege was closed, Daily Upgrade was absent and ad boost was already active. Their remaining live validation needs those states to become available.
- All 116 offline tests passed. They cover signing, real runner boundaries, phase gates, catalog/CM refresh behavior, choice selectors, partial purchase counts, daily protections and privacy. Existing proven Arena/Rumble/normal Adventure/mail/Nixon flows were not repeatedly live-tested.
