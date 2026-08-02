# S06 — Logic modification with safety disabled

## Analyst briefing

*Read this before running the scenario. The answer key is a separate file
— don't open it yet.*

Nothing has alarmed. That's the report: the on-call engineer noticed the
plant's Modbus point map briefly stopped responding around 02:14 — a few
seconds of connection resets, then everything came back and read exactly
as it always does. No fault, no alarm, no operator complaint. She almost
didn't log it.

Several hours later, the tank overflowed. `ALARM_HORN` sounded right on
schedule at the usual high-high threshold. The pump kept running anyway.

You have the sensor capture, and access to `plc/logic/cedar_hollow.st`
(the program that's supposed to be running) and whatever program is
*actually* running, pulled from OpenPLC after the incident.

## Questions to answer

1. What happened at 02:14? What does a PLC runtime restart look like on
   the wire, and why would it be so brief?
2. Diff the two ST programs. What changed? How many lines?
3. The alarm fired on schedule. The pump didn't stop. Both of those are
   supposed to be driven by the same trip condition. Why did one work and
   not the other?
4. Was this attack "in scope" for a signature-based network IDS? Explain
   your answer using what you know about what the wire traffic looked
   like both before and after 02:14.
5. What credential did the attacker need, and where would a real Cedar
   Hollow have documented it?
6. Compare this scenario to S03. Both end with an overflowing tank and a
   damaged pump. What is actually different about the two attacks, and
   why does that difference matter for how you'd write the incident
   report?
7. What single control would have caught this *before* the tank
   overflowed, not after?

## Running it

```bash
make up                                     # the real OpenPLC stack
.venv/bin/python -m attacker.s06_logic_modification
```

The scenario performs the suspected controller change and accelerates
the plant so the delayed safety consequence can be investigated during
a lab period. If it times out, re-run with a longer `--timeout`, or
restart the stack (`make down && make up`) so the tank starts lower.

Diff the two programs yourself:

```bash
find plc/logic -maxdepth 1 -name '*.st' -print
```
