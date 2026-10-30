# AT Commander 1.7.28 — earned rewards and saved deck selection

Inventory / Mail now has **Claim earned rewards** and **Auto-claim rewards**.
Automatic cleanup checks after startup and every 15 minutes while account-changing
work is idle. Stop all disarms it until restart, a manual claim, or toggling the
checkbox. It collects completed quests, earned collection milestones, and available
Mega Quest / Swole / Clash / Melee / ECMC / SFC milestone rewards. Pending reward
choices use the existing Rogu, then Characters, then Items priority. It does not
purchase tiers, open inventory packs or delete mail. Startup popup presentation
still depends on the existing game-panel popup handler; this API cleanup does not
promise to dismiss every announcement or previously claimed client-side animation.

Claims are saved before transmission. Successful responses and fresh progress are
checked; interrupted claims are reconciled without replay. Claimed quests can
vanish from the active list, which is recognized only with a saved successful
response. Older missing quests without acknowledgment are retained and skipped
while unrelated earned rewards remain available. Collection messages and field
names were checked against the official 1.183 public client before implementation.

Deck selections are sorted by name and retain both the saved name and server ID.
A unique saved name can resolve a stale ID; ambiguous names do not silently select
a different deck. IDs are server keys, not screen positions. Live account reads
identified ArcherFood as ID 20 and Disguised Combo as ID 24; neither deck was
renamed or overwritten. The workshop adds **Use current game deck** to refresh and
preview the actual active saved deck, plus **Use selected deck for battles** to
set its local offense selectors across battle modes. Defense selections are kept.

The Events controls and activity layout from 1.7.27 are preserved. Reward controls
live inside Inventory / Mail, without adding another global header row.
