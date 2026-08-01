# OpenPLC integration notes (M1.5)

**Status: built and verified**, not just researched. `plc/openplc/Dockerfile`
builds real OpenPLC from pinned source; `process_sim/server.py --field-only`
+ `plc/modbus-map-field.yml` is the field device half of the split;
`plc/logic/cedar_hollow.st` is the real compiled control logic;
`tools/openplc_configure.py` scripts bring-up over OpenPLC's own HTTP
routes; `tests/test_openplc_integration.py` proves all three rungs and
the S06 attack path against real containers. `make up` brings up the
whole stack. Everything below this point is the original research that
guided the build — kept because it's still the accurate reference for
the addressing scheme and the webserver routes.

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

## Architecture split — as built

The M1 combined process (`process_sim/server.py` default mode, still
used by S01/S03/detection tests) is unchanged. The split exists
alongside it as `--field-only` mode plus real OpenPLC:

```
  process-sim --field-only (Modbus SLAVE, "field I/O")
    plc/modbus-map-field.yml — its own independent address space
    input registers  : LT_101, FT_201, AIT_301, IT_101   (sensors)
    discrete inputs  : LSHH_101, LSLL_101, P101_FB, P101_FAULT (hardwired)
    coils            : P101_RUN, V201_OPEN, CL301_RUN, ALARM_HORN (actuators)
    holding registers: SP_P101_SPD, SP_CL_DOSE (analog outputs — these
                        two are genuinely field-side: real signals to a
                        VFD speed reference and a metering pump)
        ^ polled by, offset 100 (see mapping table above)
  OpenPLC (Modbus MASTER to process-sim; Modbus SLAVE to HMI/attacker on 502)
    plc/logic/cedar_hollow.st — the three rungs, real compiled ST
    owns MODE_AUTO (%QX0.0) and SP_LVL_HI/SP_LVL_LO/SP_ALM_HH (%QW0-2) —
    no physical wire, so no reason for the field device to know about them
```

`plc/modbus-map-field.yml` is a genuinely separate file from
`plc/modbus-map.yml`, not a filtered view of it — a real field device
has its own independent address space, and building it that way meant
zero risk to the already-tested M1 point map code.

## Verified gotcha, undocumented upstream: no DNS in the Modbus master

OpenPLC's compiled Modbus master resolves configured device addresses
with libmodbus's `modbus_new_tcp()` → `_modbus_tcp_connect()`, which calls
plain `inet_addr()` on the configured address string:

```c
addr.sin_addr.s_addr = inet_addr(ctx_tcp->ip);
```

`inet_addr()` parses dotted-decimal IPv4 only — it does **not** resolve
hostnames. Configuring a slave device with `device_ip=process-sim` (a
perfectly normal docker-compose service name) silently becomes
`INADDR_NONE` (`255.255.255.255`), and the master then fails every poll
with `Connection failed on MB device ...: Network is unreachable` — a
confusing error with no hint that the actual problem is DNS.
`tools/openplc_configure.py` works around this by resolving the hostname
to a literal IP itself (`socket.gethostbyname`) before submitting the
device form, so a compose service name works as *input* even though
OpenPLC itself can never see it.

## Verified: the S06 attack path, end to end

`tests/test_openplc_integration.py::test_s06_program_swap_disables_interlock_even_in_auto_mode`
logs into a *live* OpenPLC instance running the safe program
(`plc/logic/cedar_hollow.st`) with real default credentials, uploads
`plc/logic/cedar_hollow_s06_no_interlock.st` (identical except rung 2,
the protective interlock, is deleted), compiles it, and restarts — the
same `/upload-program` → `/upload-program-action` → `/compile-program` →
`/start_plc` sequence a real attacker would use. Confirmed: the pump
keeps running straight through the level where the interlock used to
stop it, in auto mode, with the alarm (rung 3, untouched by the swap)
still firing the whole time. This is the decisive proof for T0843
Program Download / T0889 Modify Program / T0837 Loss of Protection.

That test proved the mechanism; `attacker/s06_logic_modification.py`
turns it into the actual S06 scenario (`make scenario-S06`), and
`scenarios/S06-logic-modification/` has the full briefing/detection/
answer-key. Verified live against the real `make up` stack, not just in
the test harness: interlock absence confirmed 33 seconds after the
attack, tank level 99.3% with the pump still running.
