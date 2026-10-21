# Looking up a verified call

## The filters

`find_call` takes any combination; a blank filter is ignored and the
filters are ANDed.

| filter | matches on |
|---|---|
| `tool` | exact tool name |
| `server` | exact server name (case-insensitive) |
| `tag` | any tag, case-insensitive |
| `query` | free text over id, server, tool, summary, notes, tags, arguments |
| `include_unverified` | also return `claimed` entries |

## What comes back

Each hit carries the full entry plus the derived `call_id` and
`is_verified` flag. In list results the `evidence` is truncated to 400
characters — use `get_call` for the whole thing.

```
find_call(tool="list_unknowns")
find_call(server="rea", tag="precondition")
find_call(query="elf headless")
```

## Reading the result

- `count` — how many matched.
- `verified_only` — whether claims were excluded. It is `true` by default.

If `count` is 0 with `verified_only: true`, try again with
`include_unverified: true`. A claim is still a lead — it tells you
somebody tried this — but you are the one who has to verify it.

## Which tools of a server are known-good

```
list_tools_of(server="rea")
```

Returns each tool with `verified_calls` and `claimed_calls` counts and the
entry ids. This is how you find out whether a server is well covered
before you start working with it.

## Coverage

```
corpus_stats()
```

Totals, servers, distinct tools, tag histogram, and the split by
`verified_by`. Use it to judge how much to trust a result.

## Version drift

Every entry records the `server_version` it was verified against. If yours
differs, the arguments are a hint rather than a guarantee. Try them, but
expect to adjust, and record the corrected version.

## Cost

`find_call` is a local file read. There is no network call, no embedding,
and no ranking model. The result set is small because the corpus is
curated, not because a search is being approximated.
