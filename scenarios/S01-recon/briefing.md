# S01 — Exposed device discovery and point enumeration

## Analyst briefing

*Read this before running the scenario. The answer key is a separate file
— don't open it yet.*

You are the (only) security analyst for Cedar Hollow, a small municipal
water system. The plant runs one pump station: a 500 m³ storage tank, a
fill pump, a discharge valve to distribution, and chlorine dosing.

Last night, an automated alert from a network sensor flagged unusual
traffic on the control network. Nothing alarmed on the HMI. The operators
report a completely normal shift — the tank filled and drained on
schedule, chlorine stayed in range, no equipment faulted.

Your job is to work out what happened and whether anything is still
wrong.

## What you have

- `logs/modbus.log` — every Modbus transaction the sensor observed
- The plant's point map (`plc/modbus-map.yml`)
- The detection baseline (`sensor/baseline.yml`) describing what normal
  traffic on this network looks like
- A live HMI you can poll (`make hmi`)

## Questions to answer

1. How many distinct hosts spoke Modbus to the PLC during the capture?
   Which of them is supposed to be there?
2. What did the unfamiliar host actually *do*? Be specific — which
   function codes, which addresses.
3. Did it change anything? How can you tell, with evidence rather than
   the absence of an alarm?
4. The attacker generated a noticeable number of error responses. Why
   would that be, and what does it tell you about their knowledge of this
   plant?
5. Nothing in the process misbehaved. Is this incident therefore
   unimportant? Justify your answer.

## Running it

```bash
make sim          # terminal 1 — the plant
make tap          # terminal 2 — the sensor
make hmi          # terminal 3 — normal HMI polling
make scenario-S01 # terminal 4 — the attack
make detect       # what the sensor caught
```
