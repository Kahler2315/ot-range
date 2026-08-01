# S05 — Expected process impact

Quiet on every screen. Loud in the physical world.

| Aspect | What's reported (`LT_101`) | What's real |
|---|---|---|
| Tank level | Frozen at 50.0% for the whole incident | Climbs from ~90% to the physical limit |
| `ALARM_HORN` | **Annunciates anyway** — it reads ground truth internally, not the spoofed wire value | Genuinely warranted |
| `LSHH_101` float | Trips true — hardwired, can't be spoofed | Genuinely tripped |
| Fill pump | Reported as commanded on (coil state isn't spoofed, only `LT_101` is) | Genuinely running, genuinely deadheading |
| HMI display | Shows a calm 50% the entire time | — |

The alarm firing despite the spoof is deliberate, not an oversight: it
reflects that `process_sim`'s internal control/annunciation logic reads
ground-truth physics directly, never the wire-facing register. Only what
gets **reported outward** is lied about. A real compromised RTU might or
might not preserve that distinction — this range models the version where
it does, because that's the version that makes the cross-check in
`detection.md` meaningful to teach.

## Timeline (at `--speed 600`, starting from 90%)

| Elapsed | Event |
|---|---|
| 0 s | Undocumented register 99 ← 5000 (arms the spoof at 50.00%) |
| 0 s | `MODE_AUTO` ← 0, `P101_RUN` ← 1 (same two writes as S03) |
| 0 s onward | `LT_101` reads exactly 50.00% on every poll, unmoving |
| ~5–15 s | Real level climbs past 95% (`SP_ALM_HH`) — `ALARM_HORN` on, `LT_101` still 50.00% |
| ~9–20 s | Real level passes 98% — `LSHH_101` float makes, `LT_101` still 50.00% |

Wall-clock times scale with `--speed`, same as S03 — this scenario is S03
plus one extra write at the start, so the physical timeline is identical.

## What a screen-only operator sees

A flat line at 50%. No alarm history to explain (the alarm is real, but
if it's the only signal and the number next to it says 50%, it reads as
a false alarm or a sensor fault, not a genuine high-high). The natural
operator conclusion is "spurious alarm, level's fine, I'll silence it and
keep going" — which is worse than no alarm at all, because it actively
teaches the operator to distrust the one signal that was telling the
truth alongside the lie.
