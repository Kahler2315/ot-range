# OpenPLC integration notes (M1.5 research)

Answers the plan's open question: *does OpenPLC's Modbus server expose the
program-download path S06 needs, or does that need a separate engineering
interface?*

Findings below were read directly from OpenPLC v3 source
(`github.com/thiagoralves/OpenPLC_v3`), not from secondhand documentation.

## Answer: program download is HTTP, not Modbus

**S06 is buildable, and it does not need a separate engineering-interface
simulation — but it is not a Modbus scenario.**

OpenPLC's Modbus server exposes process data only. Program upload,
compilation, and run/stop control all live on the Flask webserver
(`webserver/webserver.py`, default port 8080):

| Route | Purpose |
|---|---|
| `/login` | Form login, plaintext password compare against `openplc.db`, Flask-Login session cookie |
| `/upload-program` | `POST` multipart file upload, saves the `.st` into `st_files/` |
| `/upload-program-action` | Registers the uploaded program (name, description, epoch time) |
| `/compile-program` | Compiles the selected program |
| `/reload-program`, `/update-program` | Swap the active program |
| `/start_plc`, `/stop_plc` | Runtime control |
| `/point-write` | Direct write to an individual point |

Every route gates on `flask_login.current_user.is_authenticated`; there is
no CSRF token on these forms, and the shipped credentials are
`openplc` / `openplc`.

**This is better for the range than a Modbus-based download would have
been.** It matches the real-world tradecraft the scenario library is
modeled on: the documented water-sector intrusions reached *exposed
engineering interfaces protected by default credentials*, not raw
fieldbus program-download function codes. So S06 becomes a two-stage
scenario that chains naturally onto S02:

1. Reach the engineering interface, authenticate with default creds
   (S02 — `T0812 Default Credentials`, `T0822 External Remote Services`)
2. Upload and activate modified logic (S06 — `T0843 Program Download`,
   `T0889 Modify Program`)

Detection therefore lives at the HTTP layer plus a logic-integrity check,
not in `modbus.log`: authentication from an unexpected source, program
upload outside a maintenance window, runtime restart, and a checksum of
the active `.st` drifting from a known-good baseline.

## Located variable ↔ Modbus mapping

### As a Modbus slave (what an HMI or attacker sees)

From `webserver/core/modbus.cpp` (`mapUnusedIO()`), the mapping is direct
and zero-indexed — no offset arithmetic:

| Located variable | Modbus table | Address |
|---|---|---|
| `%IX0.0` … | Discrete inputs | bit 0 … (`bool_input[i/8][i%8]`) |
| `%QX0.0` … | Coils | bit 0 … (`bool_output[i/8][i%8]`) |
| `%IW0` … | Input registers | register 0 … (`int_input[i]`) |
| `%QW0` … `%QW1023` | Holding registers | register 0 … 1023 (`int_output[i]`) |
| `%MW0` … `%MW1023` | Holding registers | register **1024** … 2047 (`int_memory[i]`) |
| `%MD0` … | Holding registers | register 2048 … 4095 (32-bit, 2 regs each) |
| `%ML0` … | Holding registers | register 4096 … 8191 (64-bit, 4 regs each) |

Range constants are `MIN_16B_RANGE 1024` / `MIN_32B_RANGE 2048` /
`MIN_64B_RANGE 4096` in `modbus.cpp`.

Note `%IW` and `%IX` are *inputs* — the PLC program reads them and cannot
write them. Any process value the program computes and wants to publish
to an HMI must land in `%QW`/`%MW` (holding registers), not `%IW`.

### As a Modbus master (polling our process simulator)

From `webserver/core/modbus_master.cpp`, polled slave-device data is
mapped into located variables starting at a **fixed offset of 100**:

```c
if (bool_input[100+(i/8)][i%8] != NULL) *bool_input[100+(i/8)][i%8] = bool_input_buf[i];
if (int_input[100+i]        != NULL) *int_input[100+i]        = int_input_buf[i];
if (bool_output[100+(i/8)][i%8] != NULL) bool_output_buf[i] = *bool_output[100+(i/8)][i%8];
if (int_output[100+i]       != NULL) int_output_buf[i]       = *int_output[100+i];
```

| Slave device data | Located variable |
|---|---|
| Discrete inputs read from slaves | `%IX100.0` … |
| Input registers read from slaves | `%IW100` … |
| Coils written to slaves | `%QX100.0` … |
| Holding registers written to slaves | `%QW100` … |

`i` is a cumulative index across *all* configured slave devices, in
configuration order — so adding a device shifts every later device's
offsets. Worth pinning device order in config and asserting the mapping
in a test once this lands.

## Planned architecture split for M1.5

Current state (M1): `process_sim/server.py` is field device *and*
controller in one process. The split:

```
  process_sim (Modbus SLAVE, "field I/O")
    input registers  : LT_101, FT_201, AIT_301, IT_101   (sensors)
    discrete inputs  : LSHH_101, LSLL_101, P101_FB, P101_FAULT (hardwired)
    coils            : P101_RUN, V201_OPEN, CL301_RUN    (actuators)
        ^ polled by
  OpenPLC (Modbus MASTER to sim; Modbus SLAVE to HMI on 502)
    runs the three rungs as IEC 61131-3
    owns setpoints: SP_LVL_HI, SP_LVL_LO, SP_CL_DOSE, SP_ALM_HH, SP_P101_SPD
    owns MODE_AUTO, ALARM_HORN
```

This is the honest arrangement: the simulator owns physics and I/O, the
controller owns logic and setpoints. It also relocates the setpoints from
the field device to the PLC, which is where an attacker would actually
have to go to alter them — making S04 (setpoint drift) a PLC-directed
attack rather than a field-device one.

**Consequence for the point map:** `plc/modbus-map.yml` currently
describes one flat endpoint. It will need to describe two (field I/O vs.
controller), or grow a `device:` key per point. Deferred until the split
is actually built, so the schema is driven by a working integration
rather than guessed at.

## Not yet verified

Everything above is read from source, not observed running. OpenPLC needs
either Docker or a native install (`install.sh` requires `apt-get` and
root), neither of which is available in the environment where this
research was done. Before M1.5 is called complete, stand OpenPLC up and
confirm:

1. The offset-100 master mapping behaves as the source implies, with our
   specific slave-device configuration.
2. The three rungs, once translated to ST, reproduce the behavior the
   existing unit tests assert (auto start/stop at setpoints, interlock
   stops the pump, alarm annunciates).
3. `/upload-program` + `/compile-program` + `/start_plc` can be driven
   programmatically end-to-end, which is what the S06 attack script needs.
