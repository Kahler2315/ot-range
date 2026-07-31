(* Cedar Hollow Pump Station — control logic.
 *
 * Direct IEC 61131-3 translation of the three rungs previously run as
 * an interim measure in process_sim/server.py's Python _control_scan().
 * This is the real thing: it runs inside OpenPLC, polling the field
 * simulator (process_sim/server.py --field-only) as a Modbus slave
 * device and driving it exactly the way a real PLC drives real field
 * I/O.
 *
 * Addressing: field I/O arrives/departs at OpenPLC's fixed slave-device
 * offset of 100 (see docs/openplc-integration.md — this is how OpenPLC
 * v3's Modbus master maps polled slave data into located variables).
 * Controller-only parameters (MODE_AUTO, the setpoints) have no physical
 * signal on any wire, so they live at ordinary low addresses instead —
 * exposed only through OpenPLC's own Modbus slave interface, which is
 * what an HMI or an attacker actually reaches. *)

PROGRAM cedar_hollow
  VAR
    (* Field I/O — polled from process-sim over Modbus (slave device),
       landing at the fixed offset-100 mapping. *)
    LSHH_101   AT %IX100.0 : BOOL;
    LSLL_101   AT %IX100.1 : BOOL;
    P101_FB    AT %IX100.2 : BOOL;
    P101_FAULT AT %IX100.3 : BOOL;

    LT_101  AT %IW100 : INT;
    FT_201  AT %IW101 : INT;
    AIT_301 AT %IW102 : INT;
    IT_101  AT %IW103 : INT;

    P101_RUN   AT %QX100.0 : BOOL;
    V201_OPEN  AT %QX100.1 : BOOL := TRUE;
    CL301_RUN  AT %QX100.2 : BOOL := TRUE;
    ALARM_HORN AT %QX100.3 : BOOL;

    SP_P101_SPD AT %QW100 : INT := 7500;  (* 75.00%, x100 scale *)
    SP_CL_DOSE  AT %QW101 : INT := 120;   (* 1.20 mg/L, x100 scale *)

    (* Controller-only — no field wire, exposed via OpenPLC's own Modbus
       slave interface instead (holding registers / coils 0+). *)
    MODE_AUTO AT %QX0.0 : BOOL := TRUE;
    SP_LVL_HI AT %QW0 : INT := 8500;  (* 85.00% *)
    SP_LVL_LO AT %QW1 : INT := 4000;  (* 40.00% *)
    SP_ALM_HH AT %QW2 : INT := 9500;  (* 95.00% *)
  END_VAR

  (* Rung 1 — level control: in auto mode, start the pump at the low
     setpoint and stop it at the high setpoint. An output location
     retains its last value across scans, so leaving P101_RUN untouched
     between the two setpoints is exactly "hold current command" — the
     same set/reset semantics the Python interim version used. *)
  IF MODE_AUTO THEN
    IF LT_101 <= SP_LVL_LO THEN
      P101_RUN := TRUE;
    END_IF;
    IF LT_101 >= SP_LVL_HI THEN
      P101_RUN := FALSE;
    END_IF;

    (* Rung 2 — protective interlock: the hardwired high-high float
       unconditionally stops the pump, but only while in auto mode.
       Manual mode deliberately bypasses this — S03 disables auto mode
       first for exactly that reason. S06 removes this rung file
       entirely, so the interlock fails even from auto mode. *)
    IF LSHH_101 THEN
      P101_RUN := FALSE;
    END_IF;
  END_IF;

  (* Rung 3 — annunciation: reflects ground truth regardless of mode.
     Annunciating isn't a control action, so it isn't gated on MODE_AUTO. *)
  ALARM_HORN := LSHH_101 OR (LT_101 >= SP_ALM_HH);

END_PROGRAM


CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK Main(INTERVAL := T#100ms, PRIORITY := 0);
    PROGRAM Inst0 WITH Main : cedar_hollow;
  END_RESOURCE
END_CONFIGURATION
