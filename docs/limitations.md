# Known limitations

Documented openly rather than hidden — a limitation nobody wrote down is a
limitation somebody else discovers the hard way.

1. **Not an engineering-accurate hydraulic model.** Tank/pump/demand
   numbers are tuned so behavior is legible on a dashboard (a forced pump
   overflows the tank in a demo-friendly number of minutes at a normal
   `--speed`), not for real process engineering analysis.
2. **Control logic currently lives in Python, not a real PLC.**
   `process_sim/server.py` runs the auto start/stop, protective interlock,
   and annunciation rungs directly against the physics state. This moves
   to OpenPLC (IEC 61131-3 ladder) at M1.5 — the S06 logic-modification
   scenario isn't meaningful until that split exists.
3. **pymodbus is pinned to 3.6.9**, not the current 3.14+. 3.14 renamed
   `ModbusSlaveContext` → `ModbusDeviceContext` and replaced the datastore
   accessors used here with `SimData`/`SimDevice`; 4.0 will make that the
   only option. Most documentation and examples students find will still
   target the pre-4.0 API for a while, so the pin keeps this repo aligned
   with what's findable. Migrating to the new API is a known follow-up.
4. **The pump inflow model is simplified, not a real centrifugal curve.**
   Inflow is constant with respect to level; "deadheading" is defined as
   the pump continuing to run once the tank is already at its 100%
   structural limit — a simplification chosen so the S03 scenario (forced
   pump override → overflow → pump damage) reaches both outcomes within a
   legible timeframe, rather than modeling a real pump/system curve.
5. **No sensor, attacker, or scenario content yet.** Zone networks,
   Zeek/Suricata, attack scripts, and the scenario library are M4–M6 —
   not started as of this writing.
