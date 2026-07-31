# Known limitations

Documented openly rather than hidden — a limitation nobody wrote down is a
limitation somebody else discovers the hard way.

1. **Not an engineering-accurate hydraulic model.** Tank/pump/demand
   numbers are tuned so behavior is legible on a dashboard (a forced pump
   overflows the tank in a demo-friendly number of minutes at a normal
   `--speed`), not for real process engineering analysis.
2. **Two control-logic paths exist, and the scenario library (S01/S03)
   still targets the Python one.** `make sim` (default) runs the interim
   Python controller in `process_sim/server.py`, which is what
   `attacker/s01_recon.py`, `attacker/s03_unauthorized_command.py`, and
   the detection tests are built against. `make up` runs the real thing —
   OpenPLC executing compiled IEC 61131-3 (`plc/logic/cedar_hollow.st`)
   against `process_sim/server.py --field-only` — verified end to end
   including the S06 attack path (`docs/openplc-integration.md`), but
   S01/S03 haven't been re-pointed at it yet. Migrating the scenario
   library onto the OpenPLC stack is a deliberate follow-up, not done
   implicitly by M1.5 landing.
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
5. **The sensor is an inline proxy, not Zeek.** `sensor/tap.py` sits
   between clients and the slave and emits Zeek `modbus.log`-compatible
   records, so detection rules written against it port to a real Zeek
   deployment. But it is not Zeek: no protocol validation, no reassembly
   edge cases, none of what Suricata would add. Passive capture needs
   root or a real tap, and a Docker bridge does MAC learning so a
   bridge-attached sniffer would see nothing between other containers
   anyway — hence the proxy until the M4 router container exists.
6. **Source identity is IP-based, and Modbus has no authentication.** A
   spoofed source address defeats every source-based detection rule here.
   That is a faithful reflection of the protocol rather than a modelling
   shortcut, and it is the reason segmentation matters more than
   detection — but teaching material should say so rather than imply the
   rules are stronger than they are.
7. **Reconnaissance detections fade as the attacker learns.**
   `MODBUS_EXCEPTION_SPIKE`, `MODBUS_POINT_ENUMERATION` and
   `MODBUS_UNIT_ID_SWEEP` all measure the attacker's *ignorance* of the
   plant. Narrow the sweep to points that exist and they go quiet. See
   [`coverage-matrix.md`](coverage-matrix.md).
8. **Zone networks and the containerised stack don't exist yet.**
   Everything runs on loopback, with distinct `127.0.0.0/8` source
   addresses standing in for separate hosts. Real Purdue-style zone
   separation is M4.
9. **No Sigma rule export.** Detection rules are Python. Sigma output is
   planned so the rules are portable to other analyst stacks.
