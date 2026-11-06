# AT Commander 1.7.32 — Discord event result summaries

Siege/Rumble timer and phone-run completion DMs now list each opponent and its
actual result, followed by totals for that run:

Rumble completed
Vs X Win
Vs Y Loss
Total 1 wins 1 losses

Only confirmed boolean outcomes count as wins or losses. Unconfirmed outcomes,
including a fight interrupted by an error, are shown separately. Stopped/error
runs keep their partial results with an accurate heading. Long summaries split
into multiple Discord messages instead of dropping results or the final total.
Opponent names are flattened and escaped for display. Battle logic, requests,
schedules, default decks and ad behavior are unchanged.

Validation: focused result/partial-error/multipart checks and the existing remote
control checks pass. No live fights or Discord messages were needed for testing.
