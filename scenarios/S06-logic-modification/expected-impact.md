# S06 — Expected process impact

Two phases, cleanly separated in time: an invisible one, then a loud one.

## Phase 1 — the compromise (invisible)

| Aspect | Effect |
|---|---|
| OpenPLC web session | Attacker logs in with `openplc` / `openplc` |
| Uploaded program | `plc/logic/cedar_hollow_s06_no_interlock.st` — rung 2 (the interlock) deleted, rungs 1 and 3 untouched |
| Runtime | Restarts to load the new program — a few seconds of Modbus connection resets on the wire, nothing else |
| Process | No visible change at all. Same pump behavior, same alarm behavior, same everything — until the plant reaches a level it has never had a reason to reach yet |

Nothing here trips an alarm, because nothing here is a process
deviation. The plant is behaving exactly as configured. It's just no
longer configured the way anyone signed off on.

## Phase 2 — the trigger (loud, and delayed)

Reaching phase 2 requires the tank to actually hit high-high level with
the pump running in auto mode — normally a rare condition, since rung 1
already stops the pump at `SP_LVL_HI` (85%) well before that. The
scenario script accelerates this by also raising `SP_LVL_HI` to 99.90%
over Modbus — the same kind of unauthenticated write S03 uses — so the
demo doesn't have to wait on a real fill cycle.

| Elapsed (from a mid-fill starting level) | Event |
|---|---|
| 0 s | `SP_LVL_HI` ← 99.90%, rung 1 no longer stops the pump anywhere near its old setpoint |
| — | Level climbs past 95% — `SP_ALM_HH` — `ALARM_HORN` fires (rung 3, untouched, working correctly) |
| — | Level passes 98% — `LSHH_101` (hardwired float) makes |
| — | **Pump is still running.** Rung 2 doesn't exist to stop it |
| — | Level continues to 100% — tank overflows, same physical endpoint as S03 |

Wall-clock timing depends entirely on where the tank happened to be when
the attack landed — this is what "latent" means. A real occurrence could
sit dormant for days before the plant's own operating pattern reaches
the condition on its own, with no `SP_LVL_HI` write needed at all.

## The one honest wrinkle in this pair of files

`cedar_hollow_s06_no_interlock.st`'s own header comment describes the
change as "one missing IF block, easy to miss in a code review." A
literal `diff` against `cedar_hollow.st` shows something bigger than
that — most of the surrounding *comments* were also rewritten, because
these are two independently-authored teaching files, not a real
attacker's edit of a real file. A real T0889 attack would leave the
comments alone to actually blend in; diffing these two specific files
looks more obviously suspicious than a real stealthy edit would. The
*functional* change really is just rung 2's four lines — see the answer
key for how to separate that from the comment noise.

## What's identical to S03

Tank overflow, pump deadheading, the same 31.5 A → 42 A current climb,
the same thermal-overload latch, the same recovery requiring a person on
site. If you only look at the process outcome, S03 and S06 are
indistinguishable. The entire point of this scenario is that the *cause*
and the *evidence trail* are completely different, which is exactly what
question 6 in the briefing is asking you to work out.
