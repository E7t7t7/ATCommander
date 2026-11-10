# AT Commander 1.7.34 — daily client checks and simpler routines

The existing official game-client checker now runs automatically once per local
calendar day while Commander is open. The first check is eligible 15 seconds
after launch; an app left open checks the new day automatically. Attempts are
saved locally before launch, so restarting does not repeat that day’s automatic
check. Manual checks remain available any time and count toward the daily check.
An automatic tick never cancels an active check. Failed/cancelled attempts can be
retried manually; they do not loop every second. Stop all suspends automatic
checking for the current session until a manual check or restart. The same public
compatibility verification and cancellation behavior are used; no account, combat
or GitHub application-update action is started by this daily check.

Automation now opens with a profile dropdown, Run routine, Stop, Preview and a
Run once / Repeat choice. Repeat intervals use minutes. Common daily limits are
visible: total battles and Arena/Adventure energy reserves, plus event/ad options.
Per-run spending/research totals remain visible. Detailed Nixons/CM/research/Swole
limits and custom step editing are under More settings. Save limits persists
changes for both Best Daily and selected custom daily profiles. Save as remains
available, while profile deletion and custom step tools are in the details area.
The activity log stays visible in a resizable pane and controls scroll when needed.
Run/stop/preview states use readable status messages.

Existing saved profiles, budgets, daily planning, command handlers, resource
confirmation and routine repeat behavior are preserved. No routine is started by
opening or updating the app. All 1,447 checks pass, including once-per-day behavior
across restarts, continued manual checking, minimum-window layout, repeat-minute
conversion and custom profile limit persistence. No live battles or purchases
were needed for verification.
