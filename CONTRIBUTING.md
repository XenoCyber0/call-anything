# Contributing

The corpus is the product. An entry only counts if it was **actually
executed**.

## Adding a call

### Option A — let the seeder do it (preferred)

```bash
python3 tools/seed_from_server.py \
  --server rea --cmd "node /path/to/rea/scripts/rea.mjs mcp" \
  --call list_unknowns \
  --summary "List residual unknowns for the open target" \
  --tags "rea,unknowns"
```

The seeder calls the tool for real and stores the actual output. If the
call fails, nothing is recorded — that is intentional.

### Option B — by hand

Edit `data/calls.yaml`, then:

```bash
python3 tools/build_calls.py          # validate + regenerate CALLS.md
python3 tools/build_calls.py --check  # what CI runs
```

## The rules the CI gate enforces

1. An entry with `verified_by` other than `claimed` **must** carry
   `evidence` and a `verified_at` date.
2. An entry with `verified_by: claimed` **must not** carry evidence.
3. Two entries **must not** describe the same call (same server + tool +
   arguments). The identity is a hash of exactly those three things.
4. `verified_by` must be one of: `recorded`, `replayed`, `witnessed`,
   `claimed`.
5. `CALLS.md` must be regenerated and committed with the change.

If the gate rejects your entry, it is telling you the truth. Do not work
around it by weakening the entry — that is the one behaviour this project
exists to prevent.

## Filling in an entry

```yaml
- id: rea-list-unknowns
  server: rea
  tool: list_unknowns
  arguments: {}
  summary: List the residual unknowns for the currently open target
  evidence: |
    {"result": {"items": [], "total": 0, "offset": 0, "next_offset": null}}
  verified_by: recorded
  verified_at: 2026-10-21
  server_version: 6.7.0
  tags: [rea, unknowns]
```

- `evidence` — real output, trimmed to the useful part. Not a
  reconstruction, not a paraphrase, not a promise.
- `verified_by` — how it was proven. Use `claimed` if you have not run it.
- `server_version` — required for `replayed` entries.
- `tags` — lowercase, hyphenated. Tags are how people find things.

## Secrets

Never put credentials, tokens, API keys, private hostnames, or personal
paths into `evidence`. The corpus is public and copied into agent context.

## Pull requests

- One logical group of calls per PR.
- Describe what the calls do and what you verified.
- Say plainly which calls you executed and which are claims.
- CI must be green. The gate is the review checklist; do not bypass it.
