# Call-Anything corpus schema

The corpus lives in `data/calls.yaml`. It is the source of truth.
`CALLS.md` is generated from it.

```yaml
version: 1
calls:
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
    notes: ""
```

## Fields

| field | required | meaning |
|---|---|---|
| `id` | yes | unique within the corpus. Default: `<server>-<tool>` slug, suffixed `-2`, `-3` on collision. |
| `server` | yes | the MCP server the tool belongs to. Case-insensitive for lookup. |
| `tool` | yes | the tool name as the server advertises it. |
| `arguments` | no | the exact arguments that worked. `{}` for a no-argument call. Defaults to `{}`. |
| `summary` | no | one line: what this call does. |
| `evidence` | **conditional** | real output proving the call worked. Required for everything except `claimed`. |
| `verified_by` | yes | `recorded` / `replayed` / `witnessed` / `claimed`. |
| `verified_at` | **conditional** | ISO date. Required for everything except `claimed`. |
| `server_version` | no | version of the server the call was verified against. Required for `replayed`. |
| `tags` | no | lowercase, hyphenated labels used for filtering. |
| `notes` | no | free text. Never put secrets here. |

## `verified_by` — the honesty ladder

| value | means | evidence required | date required |
|---|---|---|---|
| `claimed` | someone asserted it works. **Not verified.** | must be absent | must be absent |
| `recorded` | it was executed and returned a successful result | yes | yes |
| `replayed` | it was executed again later and still succeeded | yes | yes |
| `witnessed` | a human ran it and confirmed the outcome | yes | yes |

`claimed` entries are stored but excluded from `find_call` by default and
from the `CALLS.md` index. `find_call(include_unverified=true)` returns
them, clearly marked.

## Call identity

A call is identified by the hash of exactly three things:

```
call_id = sha256(server + tool + canonical_json(arguments))[:12]
```

`canonical_json` sorts keys, so argument order does not matter. Two entries
describing the same call are a validation error — the second one is
redundant, and redundancy is how corpora rot.

## Preconditions

A call that fails because a prerequisite is missing is still worth
recording — as verified knowledge about the *prerequisite*:

```yaml
- id: rea-list-procedures
  server: rea
  tool: list_procedures
  arguments: {}
  summary: Requires an open target; call open_binary first
  evidence: |
    {"error": {"code": "target_unavailable", "category": "unavailable",
               "message": "No app is open."}}
  verified_by: recorded
  verified_at: 2026-10-21
  server_version: 6.7.0
  tags: [rea, precondition]
```

This is how the corpus teaches an agent the order of operations.

## Validation rules

Enforced by `tools/build_calls.py --check` and the test suite:

1. `id`, `server`, `tool` must be present and non-empty.
2. `verified_by` must be one of the four values above.
3. `evidence` present ⇔ `verified_by != claimed`.
4. `verified_at` present whenever `verified_by != claimed`.
5. `server_version` present whenever `verified_by == replayed`.
6. No two entries may share a `call_id`.
7. `CALLS.md` must match the regenerated output.
