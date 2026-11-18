# AT Commander 1.7.37 — event priority at safe task boundaries

Armed Siege/Rumble timers now take priority when due, rather than waiting for a
whole Arena, Adventure, Swole or event session to end. The current battle finishes,
including its outcome/progress verification. The runner then acknowledges a hold
before its next fight. Daily/Command Center play yields between battles and
commands; Research yields after its saved mutation is reconciled. New background
mutations wait while the event has priority. Future armed times do not hold work.

Existing workers wait in memory. Their completed count, original limit, reserve,
completion route, energy budget, research spending budget, selected logic and
manual pause remain intact. This is a suspension, not a stopped/recreated run.
Training/energy polling can yield while waiting. An existing event runner and its
phone result collection are retained while the scheduled runner occupies Events;
worker identity prevents another same-mode completion from closing its job.

Scheduled fights use the independent saved timer defaults. Before switching the
loadout, a suspended account's exact active deck, hero and available card identities
are captured from fresh state. On normal completion they are restored at most
once and checked from fresh state before any ordinary work resumes. Uncertain
restore replies are checked without resending. Changed original cards, unconfirmed
loadouts, failed or stopped scheduled fights and failed regular combat/routines
hold work for review. Stop All cancels workers before releasing the hold and
disarms timers, so stopped jobs cannot accidentally start their next battle.

If both timers are due, both eligible event checks run before regular tasks resume.
Siege still requires the configured fresh guild orders and allowed weekday;
missing orders are reported without holding ordinary work indefinitely or blocking
an eligible Rumble. A current non-cooperative deck/reward/browser mutation is
allowed to finish before event launch. The existing ad-only exemption remains;
Siege/Rumble never require ad completion. No uncertain combat request is replayed.
The PC must be awake and Commander open. In-memory task continuation does not
survive closing/restarting the app.

Validation: all 1,485 offline checks pass, including real runner boundary tests
for Arena and Adventure mastery, retained Adventure energy budgets and battle
counts, verified research mutation/budget continuation, sequential due timers,
loadout restoration and timeout reconciliation, stopped/error handoffs, worker
identity, suspended phone results, Stop cancellation and preserved ad exemptions.
One read-only live account refresh also confirmed that the current main account
exposes exact active deck, hero and card identities. No live fight, deck change,
research mutation or resource spending was used for verification. Live scheduler
handoff is not claimed as tested; the desktop still ran 1.7.35 during diagnostics.
