# S05 — Manipulation of view

## Analyst briefing

*Read this before running the scenario. The answer key is a separate file
— don't open it yet.*

Nothing looks wrong. That's the whole problem.

The Cedar Hollow HMI has shown a steady, unremarkable tank level for
the last several minutes. No alarm. No unusual traffic pattern an
operator would notice by eye. A shift change is coming up and the
outgoing operator is about to tell the incoming one it was a quiet shift.

You have the sensor capture covering the same window. Somewhere in it is
the answer to whether "quiet shift" is true.

## Questions to answer

1. The HMI display and the sensor capture describe the same plant at the
   same time. Do they agree? If not, which one is lying, and how do you
   know — not guess, *know* — which one to trust?
2. Determine which Modbus function code reads `LT_101`, then explain
   what that register type means for what an attacker can and cannot do
   to it compared with a coil in S03.
3. If the wire-level protocol can't be written to, where did the false
   reading actually come from? What does that imply about where the
   compromise happened?
4. Identify the independent discrete level point that does not waver
   from physical truth. What makes it different from `LT_101`
   architecturally, not just in this one instance?
5. Two separate detections exist for this incident, at very different
   points in the timeline. Find both. Which one would you *rather* be the
   one that caught it, and why?
6. This scenario reuses S03's coil writes (`MODE_AUTO`, `P101_RUN`) to
   actually drive the tank unsafe. Why does the analog transmitter need
   to be spoofed at all if the pump is already being forced on the same
   way as S03 — what does the spoof add that S03 alone didn't have?

## Running it

```bash
make sim          # terminal 1 — the plant
make tap          # terminal 2 — the sensor
make hmi          # terminal 3 — normal HMI polling
make scenario-S05 # terminal 4 — the attack
make detect       # what the sensor caught
```

Start the sim at a high level to reach the failure quickly:

```bash
.venv/bin/python -m process_sim.server --level 90 --speed 600
```

Watch `tools.modctl watch` or the HMI (`hmi/`, `docs/architecture.md`) in
a spare terminal while the attack runs — the point is to actually *see*
the display stay calm while the process doesn't.
