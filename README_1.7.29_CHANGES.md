# AT Commander 1.7.29 — Discord phone control and weekday event timers

**Discord & timers** is a new page. Connect an owner-only Discord bot inside the
normal app, save its token in Windows Credential Manager, optionally reconnect
on launch, and receive completion/failure DMs. No extra console is opened.
`/commander` supports status, stop, saved-setting Siege/Rumble runs, supported
registered commands and timer changes. Existing `/cm` and `/cmshop` reviews are
available on this connection. See DISCORD_CM_BOT_SETUP.md for setup and examples.
Discord support remains optional and can be installed from this page.

Siege defaults to **Monday**, checking every 30 minutes on that day when armed.
Rumble defaults to **Every day**. Both allow any weekday, interval 1–1440 minutes,
and optional HH:MM first-run time in the PC’s local timezone. Inactive Siege checks
return without ads, deck changes or waiting for the battle phase. A busy PC queues
one due check rather than overlapping workers or replaying missed intervals.
An active Siege run uses the existing verified attack phase, living target,
energy and A/B/C fallback logic; manual Siege monitoring keeps its prior behavior.
Armed idle schedules survive restart. A run interrupted by process exit pauses
its timer for review rather than restarting a potentially uncertain fight.
Stop all disarms timers; ordinary close preserves idle schedules. Timers start
unarmed on first use. Neither the timer nor Discord operates while the PC sleeps
or Commander is closed. Weekly schedules only run on the selected day.

All phone actions are owner-checked, bounded and processed on the GUI thread;
duplicate interactions and cancelled queued requests do not execute twice.
Battle runs use the saved PC settings and existing workers. Destructive,
purchase and outbound-message registry commands require the PC. Completion
messages contain fixed summaries, not raw account responses or credentials.
The Events layout and existing automation behavior are preserved.

Also completes the pending Adventure HTTP fix: account/map/outcome reads retry
only classified transient network failures, with 5/15/30-second cancellable waits.
HTTP status numbers are retained without request URLs or private request fields.
Starts and card plays are still sent once. Stop keeps the established final
verification of an already charged fight. No live battles or Discord messages
were sent while testing this release; connection requires the user’s own bot.
