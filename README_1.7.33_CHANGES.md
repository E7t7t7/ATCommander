# AT Commander 1.7.33 — Siege/Rumble always ignore watched ads

Siege and Rumble already skipped the boost/ad prerequisite, but their UI and
remote/timer launch guards still treated an active ad watcher as exclusive work.
These modes now also bypass a specifically identified Watch Ads worker and an
ad-only user-ad command. This applies to Events launches, phone commands,
scheduled event checks, Siege defense setup, and command/routine batches made
exclusively of Siege/Rumble play commands. Event workers never require or launch
ad watching. No ad reward or completion is fabricated.

The exemption identifies the actual watch worker; calibration, diagnostic capture,
game startup, research, deck edits and other mutations retain their normal guards.
Other game modes retain their prior ad and overlap behavior. Command Center still
runs one command/routine batch at a time, while separate Events/phone/timer paths
can start their event worker during an ad-only command. Stop all continues to
stop both workers. The result summaries and saved loadouts remain unchanged.

All 1,440 checks pass, including inactive-boost event starts, watcher identity,
user-ad command classification and preservation of unrelated worker protection.
No live fights or ad operations were needed for verification.
