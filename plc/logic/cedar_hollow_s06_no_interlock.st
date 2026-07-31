(* S06 — logic modification with safety disabled.
 *
 * Identical to plc/logic/cedar_hollow.st with exactly one change: rung 2
 * (the protective interlock) is deleted. Rung 1 (normal pumping) and
 * rung 3 (annunciation) are untouched — the plant looks and behaves
 * completely normally under ordinary operation. Nothing breaks until
 * the process reaches the exact condition the interlock existed to
 * catch: the tank at high-high level with the pump still running, in
 * auto mode, where the pump previously would have been forced off.
 *
 * This is the file an attacker uploads via OpenPLC's web interface
 * (T0843 Program Download) after reaching it with default credentials
 * (T0812, chained from S02). Compare against cedar_hollow.st with a
 * plain diff — the change is one missing IF block, easy to miss in a
 * code review that isn't specifically looking for it. That's the point:
 * T0889 Modify Program / T0837 Loss of Protection / T0880 Loss of Safety
 * describe exactly this kind of change, not an obviously broken program.
 *
 * See docs/openplc-integration.md and scenarios/ (S06 briefing pending —
 * this file exists once OpenPLC is verified running for real; the
 * scenario writeup follows once that's proven end to end). *)

PROGRAM cedar_hollow
  VAR
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

    SP_P101_SPD AT %QW100 : INT := 7500;
    SP_CL_DOSE  AT %QW101 : INT := 120;

    MODE_AUTO AT %QX0.0 : BOOL := TRUE;
    SP_LVL_HI AT %QW0 : INT := 8500;
    SP_LVL_LO AT %QW1 : INT := 4000;
    SP_ALM_HH AT %QW2 : INT := 9500;
  END_VAR

  (* Rung 1 — level control. Unchanged. *)
  IF MODE_AUTO THEN
    IF LT_101 <= SP_LVL_LO THEN
      P101_RUN := TRUE;
    END_IF;
    IF LT_101 >= SP_LVL_HI THEN
      P101_RUN := FALSE;
    END_IF;

    (* Rung 2 (protective interlock) deleted. *)
  END_IF;

  (* Rung 3 — annunciation. Unchanged — the operator still sees the
     alarm. They just no longer get the automatic protective action that
     used to go with it. *)
  ALARM_HORN := LSHH_101 OR (LT_101 >= SP_ALM_HH);

END_PROGRAM


CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK Main(INTERVAL := T#100ms, PRIORITY := 0);
    PROGRAM Inst0 WITH Main : cedar_hollow;
  END_RESOURCE
END_CONFIGURATION
