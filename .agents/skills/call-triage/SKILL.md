---
name: call-triage
description: Use when calling an unfamiliar or schema-ambiguous MCP tool. Looks up a verified working invocation before guessing arguments, and records the call afterwards so the next agent does not rediscover it.
---

# Call triage

You are about to call an MCP tool you have not used successfully in this
session. Do not guess its arguments. A schema says what a call *may* look
like; the corpus says what *actually works*.

## 1. Profile the tool (cheap, no network)

Take the `tools/list` entry for the tool and run `call_profile` on it.

```
call_profile(tool_schema={"name": "...", "inputSchema": {...}})
```

Read the result:

| result | what to do |
|---|---|
| `callable_bare: true`, `unguessable: false` | just call it with `{}` |
| `total_guessing_cost: 0`, `unguessable: false` | call it with the required args only |
| `unguessable: true` | **look for a verified call first** |

`total_guessing_cost` is the number of wrong answers the schema leaves
open. If it is 0, you are guessing correctly by construction.

## 2. Look for a verified call

```
find_call(tool="<tool name>")
find_call(server="<server>", query="<what you are trying to do>")
find_call(tag="elf")
```

A hit gives you:
- `arguments` — exactly what worked,
- `evidence` — the real output it produced,
- `server_version` — what it was verified against,
- `verified_at` — how old the evidence is.

If the `server_version` differs from yours, treat the arguments as a
strong hint, not a guarantee.

## 3. Call it

Use the arguments from the entry. If it fails, the entry is stale for your
environment. Say so — do not silently work around it.

## 4. Record what worked

After a successful call, record it:

```
record_call(
  server="<server>",
  tool="<tool>",
  arguments={<what you actually passed>},
  evidence="<excerpt of the real result>",
  summary="<one line>",
  server_version="<version>",
  tags=["<tag>"]
)
```

`evidence` must be output you actually received. Never reconstruct it.

## Preconditions are knowledge too

A call that fails with `target_unavailable` or `not open` still tells you
the order of operations. Record it with the failure as evidence and tag it
`precondition`. The next agent then knows to open the target first.

## Never

- fabricate `evidence`
- record secrets, tokens, or personal paths
- treat a `claimed` entry as verified (they are excluded by default)
- open a pull request yourself — `record_call` writes locally only
