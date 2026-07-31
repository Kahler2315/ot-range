# S03 — Unauthorised command, tank overflow

## Analyst briefing

*Read this before running the scenario. The answer key is a separate file
— don't open it yet.*

06:42. The Cedar Hollow operator calls: the storage tank is overflowing
onto the yard, the high-level alarm is sounding, and the fill pump has
just faulted out. He says he did not start the pump. He also says the HMI
showed the plant in **manual** mode, which nobody put it in.

The plant is in a bad state right now:

- Tank at 100%, water spilling
- Fill pump tripped on overload and unavailable
- High-high float switch made
- Alarm annunciating

You have the sensor capture covering the last hour.

## Questions to answer

1. Reconstruct the timeline. What was the first hostile action, and what
   was the last normal one?
2. The pump ran continuously past its stop setpoint of 85%. The control
   logic is supposed to prevent exactly that. Why didn't it?
3. Two coils were written. Identify both and explain what each one did —
   why were *two* needed rather than one?
4. The high-high float switch is a separate device from the level
   transmitter. Did it work? What does your answer imply about relying on
   a single measurement path?
5. What did the attacker need to know in advance to do this, and where
   would they have got it?
6. The pump is damaged. Walk through the physical mechanism — why does
   forcing a pump on damage it, when running the pump is its normal job?
7. What single control would have prevented this? Be specific, and state
   its cost.

## Running it

```bash
make sim          # terminal 1 — the plant
make tap          # terminal 2 — the sensor
make hmi          # terminal 3 — normal HMI polling
make scenario-S03 # terminal 4 — the attack
make detect       # what the sensor caught
```

Start the sim at a high level to reach the failure quickly:

```bash
.venv/bin/python -m process_sim.server --level 90 --speed 600
```
