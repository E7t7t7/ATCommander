# AT Commander 1.7.36 — scheduled-event reliability

The saved local Rumble timer was armed and overdue without a claimed attempt.
This does not establish which live blocker caused that particular missed run.
Two code paths could leave an armed event silent: shared periodic callbacks
stopped rescheduling after an exception, and due jobs silently returned while
other automation or an unloaded deck dropdown blocked them.

The heartbeat now schedules its next call before checking subsystems and isolates
each callback failure. Event timers run first. A client-check, rewards, routine
or research callback failure cannot disable future event ticks; exceptions still
use the existing private-data-free source-location reporter.

Overdue events now retain their due time while blocked and show the blocker in
the timer state, local activity and Discord notification. Repeated identical
blockers do not spam notifications. Missing fresh Siege orders are reported,
without preventing a ready Rumble job. Idle events start when the blocker clears.
Saved timer/phone defaults no longer require populated desktop deck dropdowns:
the event worker already validates exact decks/heroes using fresh account data
before changing selections or starting fights.

Other active account mutations still block event starts; this release does not
interrupt an existing fight, research mutation, or deck edit. The established
ad-only exemption remains. Uncertain starts/combat failures and interrupted active
runs still pause without replay; Stop All still disarms timers. Timers require
Commander open and the PC awake. Weekday and interval semantics are unchanged.
No saved schedule or guild order is changed by installing this update.

Validation: all 1,460 offline tests pass, including overdue busy-to-idle launch,
deduplicated blocker alerts, empty dropdown launch, missing guild orders,
uncertain-start protection and independent heartbeat failure recovery. No live
fights or account resource spending were needed.
