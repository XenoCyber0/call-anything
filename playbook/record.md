# Recording a call without lying about it

## The seeder (preferred)

The seeder executes the call and stores the real output. It cannot record
a failure, which is the point.

```bash
python3 tools/seed_from_server.py \
  --server rea \
  --cmd "node /path/to/rea/scripts/rea.mjs mcp" \
  --call list_unknowns \
  --args '{}' \
  --summary "List residual unknowns for the open target" \
  --tags "rea,unknowns"
```

Repeat `--call` / `--args` / `--summary` for a batch; they pair by
position.

Output:

```
connected to rea 6.7.0
210 tools advertised
  OK  list_unknowns -> '{"result": {"items": [], "total": 0, ...}}'
  + rea-list-unknowns
wrote 13 entries to data/calls.yaml
```

## By hand

Edit `data/calls.yaml`, then `python3 tools/build_calls.py` to validate and
regenerate `CALLS.md`.

## `record_call` from an agent session

```
record_call(
  server="rea",
  tool="list_unknowns",
  arguments={},
  evidence="<excerpt of the real result>",
  summary="List residual unknowns for the open target",
  server_version="6.7.0",
  tags=["rea","unknowns"]
)
```

Writes locally. It does not commit, push, or open a pull request — the
repository owner does that.

It refuses a call with no evidence, and it refuses to add a second entry
for a call that is already recorded (it returns the existing entry id).

## Choosing `verified_by`

| situation | value |
|---|---|
| you ran it and it worked | `recorded` |
| you ran it again later and it still worked | `replayed` |
| a human ran it and confirmed | `witnessed` |
| someone said it works and you did not run it | `claimed` |

`claimed` entries must have **no** `evidence` and **no** `verified_at`.
The gate enforces both directions: evidence on a claim, or a claim with no
evidence, is a validation error.

## Recording a precondition

If a call fails because something must happen first, record the failure.
It is verified knowledge about the order of operations.

```yaml
- id: rea-list-procedures
  server: rea
  tool: list_procedures
  arguments: {}
  summary: Requires an open target; call open_binary first
  evidence: |
    {"error": {"code": "target_unavailable", "message": "No app is open."}}
  verified_by: recorded
  verified_at: 2026-10-21
  tags: [rea, precondition]
```

## Evidence hygiene

- Real output. Trim it, do not rewrite it.
- Never record secrets, tokens, API keys, or personal paths. The corpus
  is public and gets copied into agent context.
- A stack trace is fine. A customer name is not.

## After editing

```bash
python3 tools/build_calls.py          # validate + regenerate
python3 tools/build_calls.py --check  # what CI runs
python3 -m unittest discover -s tools -p "test_*.py"
```
