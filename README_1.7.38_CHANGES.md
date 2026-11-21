# AT Commander 1.7.38 — reachable Discord/timer and Siege settings

Discord & timers now has a clearly visible 22-pixel vertical scrollbar, a
horizontal scrollbar for narrow layouts, and always-visible page-up/page-down
buttons. Mouse wheel scrolling works over form labels, frames and buttons;
entries, comboboxes, spinboxes and the separate activity log retain their own
input behavior. The scroll region includes the full requested control width,
so narrow windows no longer silently clip the right side of the form.

A fixed navigation strip above the scrolling controls provides Connection &
timers, Siege / Rumble defaults and Guild orders shortcuts. Siege island,
attack priorities A/B/C, saved decks/heroes and Save defaults can be reached
without finding the former narrow scrollbar or dragging the activity divider.
Navigation and scrolling do not alter saved schedules, defaults or guild options.
The activity log remains separately resizable. Other pages keep their existing
scroll layout; event priority, battle logic and account behavior are unchanged.

Validation: all 1,490 offline checks pass. Five focused GUI checks verify visible
scrollbars inside a 1340x760 window, mouse-wheel routing, direct Siege/Guild
navigation, unchanged saved defaults, and a visible activity log at 1340x700.
No live battles, Discord messages, account changes or resource spending were
needed for this layout fix.
