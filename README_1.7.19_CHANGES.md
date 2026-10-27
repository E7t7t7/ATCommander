# AT Commander 1.7.19 changes

## CM Review scoring calibration

1.7.19 rebuilds the CM scorer after live validation exposed a major weakness in 1.7.18: an attractive three-skill package could score too well even when all three skill values were badly power-crept.

Changes:
- Three skills are now treated as the modern baseline, not an automatic +1 strength bonus.
- Every resolved skill magnitude is benchmarked against other current catalog combos carrying that **same skill**.
- Reviews now label skill magnitudes Low / Medium-Low / Medium / Medium-High / High.
- Skill synergy bonuses are gated by those value benchmarks. Low Craze + Low Punch no longer receives the same credit as modern competitive Craze + Punch.
- A full package of low-valued skills receives a substantial power-creep penalty.
- Attack/health multipliers are secondary tiebreakers instead of being blended with unrelated skill magnitudes into one generic power number.
- PC existence is shown as context but no longer grants free points to the combo score.
- Static recipe breadth and account-owned inputs still matter, but their scoring weight is deliberately small so they cannot rescue weak combat values.
- Mastery detection now recognizes more current `getComboMasteries` / `user_combo_mastery` list and dictionary shapes.
- Desktop and Discord continue to use the same shared scorer.

### Calibration targets from live user testing
The revised model is specifically intended to separate a badly power-crept three-skill combo such as Wingnut Amy from a still-useful modern combo such as Buckarito. Synthetic regression tests require a low-valued Sturdy/Punch/Craze package to land in the poor/pass range while a medium-high Punch/Burn/Sturdy package lands around the good/useful range.

No Arena, Events, Swole, Adventure, Inventory/Mail, ad, Nixon, or Golden Pack automation behavior was changed in this build.
