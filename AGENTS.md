# Agent contract for Call-Anything

You are using a corpus of **verified** MCP tool invocations. Each entry was
actually executed and produced a result. That is the whole value: you skip
the discovery round-trips.

## Ground rules

1. **Look before you call.** For any tool you have not used successfully in
   this session, call `find_call` first. If a verified invocation exists,
   copy its arguments.

2. **Trust the evidence, not the claim.** `find_call` returns only entries
   that carry execution evidence. If you want claims too, pass
   `include_unverified: true` — but then treat them as unproven.

3. **Respect the version.** An entry records the `server_version` it was
   verified against. A verified call on version 6.7.0 is a *hint* on 7.0.0,
   not a guarantee. If the server version differs, expect to adjust.

4. **Record what worked.** After a call succeeds, use `record_call` with the
   arguments you used and an excerpt of the real result. This is how the
   corpus grows. It is not a suggestion — an unrecorded success is a
   round-trip the next agent has to pay again.

5. **Never fabricate evidence.** `evidence` must be output you actually
   received. The CI gate rejects entries that claim verification without
   evidence, and a forged entry poisons every agent that reads it.

6. **Do not open pull requests yourself.** `record_call` writes locally.
   The repository owner commits and opens the PR. Your job is to make the
   local entry correct.

## The call contract in one table

| you want to | you call |
|---|---|
| a working invocation of a tool | `find_call` |
| the full recorded output of one entry | `get_call` |
| how hard a tool is to call, before trying | `call_profile` |
| which tools of a server have known-good calls | `list_tools_of` |
| save a call that just worked | `record_call` |
| how complete the corpus is | `corpus_stats` |

## Phases

### Phase 1 — assess

Before calling an unfamiliar tool, profile it:

```
call_profile(tool_schema=<the tools/list entry>)
```

If `unguessable` is false and `callable_bare` is true, just call it with
`{}`. If it is true, hunt for a verified call first.

### Phase 2 — look

```
find_call(server="rea", tool="analyze_binary")
```

Search by tool when you know it, by `query` when you know the problem
("elf", "headless", "permission"), by `tag` for a class of task.

### Phase 3 — call

Use the arguments from the entry. If the call fails, the entry is stale for
your environment. Report that rather than silently working around it.

### Phase 4 — record

```
record_call(
  server="rea",
  tool="analyze_binary",
  arguments={...},          # exactly what worked
  evidence="<excerpt of the real result>",
  summary="one line: what this does",
  server_version="6.7.0",
  tags=["elf","headless"]
)
```

## Phases of the corpus itself

Entries move up as they accumulate evidence:

| state | meaning |
|---|---|
| `claimed` | asserted, no execution evidence. Not served by default. |
| `recorded` | executed once, succeeded, output captured. |
| `replayed` | executed again later and still succeeded. |
| `witnessed` | a human ran it and confirmed the outcome. |

## What this corpus does not do

- It does not tell you whether a tool is the *right* tool for the job.
- It does not replace reading the schema; it saves you the round-trips.
- It does not carry secrets. Do not record credentials, tokens, or
  private paths as evidence.
