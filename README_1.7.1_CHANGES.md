# AT Commander 1.7.1

Hotfix from the user's first 1.7 screenshots/test pass.

- Ad verification now reads confirmed `getBoostLevel` instead of trusting stale/incomplete init fields.
- Rumble and Siege now each have explicit offense deck + offense hero selectors, separate from defense deck.
- Event runners apply the selected offense deck/hero before starting event fights.
- Swole status is populated from live `getHeroSquad` task data instead of a fake hard-coded Ready label.
- Hero labels include a safe static fallback for known commander IDs when the game's static URL catalog is unavailable.
- Adventure Refresh Progress now performs a live account scan instead of showing the placeholder dialog.
- Learned spend-item/Nixon profiles support dynamic IDs where the captured API exposes them; Nixon purchase IDs are no longer silently frozen when a pack/product ID field is present.
- Upgrade-card learner scoring is strengthened for upgrade/unit/card endpoints.
- Legacy per-card rows from old persistent snapshots are suppressed from Stats Total Gains.

The ad code does not fabricate ad completion; it only verifies the legitimate game's boost state.
