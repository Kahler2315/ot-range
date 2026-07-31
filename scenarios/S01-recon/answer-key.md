# S01 — Answer key

*Instructor notes. Don't hand this out with the briefing.*

## 1. How many hosts, which belongs?

Two.

- `127.0.0.1` — the HMI. In the baseline allowlist, polls a fixed set of
  18 points on a fixed interval, read-only.
- `127.0.0.2` — not in the baseline. This is the attacker.

Find it with:

```bash
grep '"pdu_type": "request"' logs/modbus.log | jq -r '."id.orig_h"' | sort | uniq -c
```

The point to draw out: the analyst did not need to know what "bad" looks
like. They needed to know what *normal* looks like, which for a control
network is a knowable, finite thing.

## 2. What did it do?

Three phases, visible in order in the log:

1. **Port probe.** A bare TCP connect to 502/5502 — is anything there?
2. **Unit ID walk.** `READ_HOLDING_REGISTERS` at address 0 against unit
   IDs 0 through 5, to find which slave IDs answer.
3. **Register sweep.** Addresses 0–7 across all four tables (coils,
   discrete inputs, input registers, holding registers), one address per
   request.

Reading singly rather than in blocks is the giveaway. No engineered
system polls that way — it is what you do when you don't yet know the
point map.

## 3. Did it change anything?

No. Every request is a read function code (1, 2, 3, 4). There is not a
single 5, 6, 15, or 16 in the attacker's traffic.

```bash
jq -r 'select(."id.orig_h"=="127.0.0.2" and .pdu_type=="request") | .func' \
  logs/modbus.log | sort | uniq -c
```

The evidence is the *function codes in the capture*, not the absence of
an alarm. Students who answer "nothing changed because no alarm fired"
have made exactly the mistake S05 later punishes.

## 4. Why the errors?

The sweep asks for addresses beyond the ones this device implements. The
plant has 5 coils, 4 discrete inputs, 4 input registers, 5 holding
registers; the attacker asked for 8 of each. Every request past the end
of a table returns `ILLEGAL_DATA_ADDRESS`.

Roughly 37% of the attacker's responses are exceptions, against ~0% for
the HMI. That gap is a measure of who knows the plant and who is learning
it — and it is transient. Re-run the recon with `--sweep-count 4` and the
exception rate collapses, because now the attacker is only asking for
points that exist. A detection built *only* on error rate degrades as the
attacker's knowledge improves, which is why it is the weakest of the four
rules here and why it is rated medium rather than high.

## 5. Is it unimportant because nothing broke?

No — and this is the discussion worth having.

What the attacker now holds is the plant's point map: which coil runs the
pump, which coil takes the plant out of automatic control, which holding
register is the high-high alarm limit. Every later scenario is cheap once
you have that. S03 is two writes.

Reconnaissance is the phase where the defender still has options and the
attacker has done nothing irreversible. It is also the noisiest phase —
the exception spike, the unit sweep, the unknown host all land here. An
organisation that only investigates incidents with visible process impact
throws away the loudest, earliest, most actionable signal it will ever
get, and meets the same actor later with the tank already spilling.

## Common wrong turns

- **"The HMI is compromised."** The HMI's traffic is unchanged throughout.
  `tests/test_attack_detection.py::test_s01_recon_does_not_implicate_the_hmi`
  exists to keep the detections from making this mistake.
- **"There's no incident, nothing happened."** See above.
- **"Block 127.0.0.2."** Reasonable instinct, wrong scope — that address
  is wherever the attacker happens to be standing today, not what let
  them in. The finding to escalate is that an unlisted host could reach
  the control network and speak Modbus to a PLC at all.
