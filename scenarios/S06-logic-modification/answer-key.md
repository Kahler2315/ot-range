# S06 — Answer key

*Instructor notes. Don't hand this out with the briefing.*

## 1. What happened at 02:14?

A PLC runtime restart. OpenPLC's `/compile-program` and the subsequent
program load stop and restart the Modbus slave/master processes, so
every open Modbus session — the HMI's, the historian's, anything polling
— sees a connection reset and has to reconnect. It's brief (seconds)
because that's genuinely all a program swap takes; there's no reason for
it to look like anything more dramatic than a network blip, which is
exactly why the engineer almost didn't log it.

## 2. Diff the two programs

```bash
diff plc/logic/cedar_hollow.st plc/logic/cedar_hollow_s06_no_interlock.st
```

The *functional* change is four lines — rung 2 in its entirety:

```
IF LSHH_101 THEN
  P101_RUN := FALSE;
END_IF;
```

A literal `diff` shows more than that, because most of the surrounding
*comments* were also rewritten between the two files. That's an artifact
of these being two independently-authored teaching files rather than a
real attacker's edit — worth flagging to students who take the diff size
at face value. A real T0889 attack preserves comments and formatting
specifically to minimize what a diff shows. If you want students to
practice isolating the real change from noise, have them strip comments
first (`grep -v '^\s*(\*'` gets close) before diffing.

## 3. The alarm fired, the pump didn't stop — why?

Because they're driven by different rungs, and only one still exists.

Rung 3 (annunciation) reflects ground truth unconditionally — it isn't
gated on anything, and it wasn't touched. `ALARM_HORN` sounding on
schedule is rung 3 working exactly as designed.

Rung 2 (the interlock, `IF LSHH_101 THEN P101_RUN := FALSE`) is the rung
that stops the pump. It isn't malfunctioning. It doesn't exist. There is
no code path left that can set `P101_RUN` false in response to the
float — the program the PLC is running was never told to do that.

This is the core distinction from S03: there, the interlock rung is
present and correct, but *stood down* by a mode flip. Here, the rung is
gone. "Broken" and "deleted" produce the same symptom and require
completely different remediation.

## 4. Was this in scope for a signature IDS?

No, and not because the signature was weak — because the attack never
touches the protocol the IDS is watching. The compromise happens over
HTTP, to OpenPLC's web interface, and nothing in this range inspects
HTTP traffic at all (see `detection.md`). The only Modbus-visible
artifact is a few seconds of connection resets during the restart, which
looks identical to a legitimate maintenance restart, a network blip, or
a container recreation — nothing a signature or an anomaly rule could
distinguish from routine.

If the accelerating `SP_LVL_HI` write is used, *that* write is visible
and would fire the existing S03-style rules — but by the time it
happens, the interlock is already gone. Catching that write catches the
tail of the attack, not the attack.

## 5. What credential did the attacker need?

`openplc` / `openplc` — OpenPLC's actual shipped default, unchanged.
Documented in OpenPLC's own public setup instructions, which is exactly
the problem: it's not secret, it's not guessed, it's published, and it
works unless someone deliberately changes it during commissioning. This
range's own `SECURITY.md` and the CISA advisories this project is
grounded in (see `docs/architecture.md`) describe this as a live,
recurring finding in real water-sector deployments, not a hypothetical.

## 6. Compare to S03 — what's actually different, and why does it matter for the incident report?

| | S03 | S06 |
|---|---|---|
| Access needed | Modbus/502 reachability only | OpenPLC web UI (8080) + default creds |
| What's changed | Nothing — a mode coil and a command coil are written | The control program itself |
| Visible on the wire | Two ordinary writes, the whole attack | A few seconds of connection resets; the writes (if any) are incidental |
| Timing | Immediate | Latent — can sit dormant indefinitely |
| Persists after network access is revoked? | No — writes stop working, plant reverts on next scan | **Yes** — the malicious program keeps running until someone re-uploads the real one |
| Effective mitigation | Segment/allowlist who reaches TCP/502 | Segmentation doesn't help — this never depended on ongoing Modbus reachability during the dormant phase. Needs program integrity checking and a locked-down engineering interface |

The persistence line is the one that most changes an incident report.
Revoking the attacker's network access ends S03 immediately. It does
nothing for S06 — the plant keeps running the malicious program, quietly
correct until it isn't, until someone specifically checks *what program
is loaded* against a known-good copy. "We cut off their access" is a
complete remediation for S03 and a false sense of security for S06.

## 7. What single control would have caught this before the tank overflowed?

**Program/logic integrity checking** — hashing the running program on
a schedule (or on every restart) and alerting on drift from a known-good
value. This is the one control that catches the actual attack, not its
downstream physical consequence, and it's the standard real-world answer
to T0889 for exactly this reason.

Other defensible answers, with their limits:

- **Change the default credential.** Necessary, not sufficient. Closes
  this specific path; does nothing against an attacker who reaches the
  web UI through a compromised jump host or a leaked real credential.
- **Restrict who can reach OpenPLC's web UI at all** (network-level, the
  same idea as S03's segmentation answer, applied to port 8080 instead
  of 502). Real and valuable, but this range's own M4 topology already
  demonstrates the limit of that approach: segmentation controls
  *reachability*, not what happens once reachability is legitimately
  granted to whoever administers the PLC.
- **Change control / maintenance windows.** Organizationally sound —
  flag any program upload outside an approved window — but detective,
  not preventive, and only as good as whether anyone's watching.

## Common wrong turns

- **"The alarm system is broken."** It isn't. Rung 3 fired exactly on
  schedule. Conflating "the alarm sounded and nothing happened" with "the
  alarm failed" is the single most common misread of this scenario.
- **"This is the same as S03, just with extra steps."** The process
  outcome is identical; the attack surface, the persistence, the
  detection story, and the fix are all different. Treating them as
  interchangeable in an incident report means recommending S03's fix
  (network segmentation) for an attack it wouldn't have stopped.
- **"Patch OpenPLC."** There's no vulnerability to patch. Default
  credentials and unauthenticated program upload are the documented,
  intended behavior of the product as shipped. The fix is operational
  (change the credential, restrict access, verify program integrity),
  not a software update.
