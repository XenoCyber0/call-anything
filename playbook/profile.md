# Profiling a tool

`call_profile` turns a `tools/list` entry into a statement about how much
guessing it demands.

## The numbers it returns

| field | meaning |
|---|---|
| `required` | arguments that must be present |
| `optional` | arguments that may be present |
| `defaults` | arguments with a default the server will apply |
| `enums` | every enum surface, with its values and whether it is communicable |
| `free_text_required` | required string args the schema does not constrain |
| `min_arity` | fewest arguments that could form a valid call |
| `callable_bare` | whether `{}` is a valid call |
| `total_guessing_cost` | **how many wrong answers the schema leaves open** |
| `unguessable` | whether the schema alone can determine a working call |
| `difficulty` | `trivial` / `easy` / `moderate` / `hard` |

## Communicable enums

An enum is *communicable* — the schema pins it down — when it has a
default, an example, a `const`, or at most four values. Small closed sets
are communicable because the agent can reasonably try them. A 6-value enum
with no default is not: the agent has to pick, and picking wrong costs a
round-trip.

This is why `total_guessing_cost` is 0 for a 2-value enum and 6 for a
6-value enum with no default.

## Difficulty

| bucket | condition |
|---|---|
| `trivial` | no required args, no guessing cost |
| `easy` | guessing cost 0, no free-text required args |
| `moderate` | guessing cost ≤ 3, no free-text required args |
| `hard` | anything else |

## The measured baseline

On REA 6.7.0 (210 tools):

- 592 properties across all tools
- **0** properties carry an `example`
- 156 carry a `default`
- 608 enum decision points, 4,641 enum values
- **161 tools (76.7%)** have at least one unguessable choice
- 13 tools are callable with no arguments at all

So on a real toolset, roughly three out of four tools have a choice the
schema cannot communicate. That is the gap the corpus fills.

## Why not just add `example` to every schema?

Because schemas are the *server author's* job and there are thousands of
servers; the corpus is the *agent user's* job and there are millions of
agents. Also, an example in a schema is a claim. An entry in this corpus
is a claim plus the recorded output that proves it.

## Use it

```
call_profile(tool_schema=<the tools/list entry>)
```

Copy the entry exactly as the server advertised it — the analyzer reads
`inputSchema.properties`, `required`, `enum`, `oneOf`/`anyOf`/`allOf`,
`default`, and `const`.
