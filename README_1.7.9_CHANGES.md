# AT Commander 1.7.9

Emergency Siege patch for the live 2026-09-28 window.

- Siege learning now saves the short API call sequence immediately preceding a successful `fightGuildSiege` start, not only the final `slot_id` request.
- Siege automation replays that captured setup sequence in order before `fightGuildSiege`, substituting the live A/B/C target slot and related target IDs.
- Removed dependence on stale Chromium `Network.replayXHR` request IDs for newly learned Siege profiles.
- Arena and Swole scheduling logic are unchanged.
