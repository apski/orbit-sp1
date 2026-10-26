You check a bug report for leaks. The engineer reading it must NOT be told the fix.

Bug report:
{{TASK}}

The real fix it was written from:
{{FIX_DIFF}}

leaks = true if the report gives away the DIAGNOSIS or the FIX: the internal cause (which value/type is wrong,
which lookup or branch misbehaves, why it happens), the line/condition/branch to change, the data structure or
algorithm change, a code snippet, or wording that maps one-to-one onto the diff. Fine: observed vs expected
behaviour, how to trigger it, the entry point a caller uses, impact, and an `interface` the tests need.
