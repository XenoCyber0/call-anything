# Call-Anything

**The corpus of VERIFIED working MCP tool invocations.**

An MCP server's `inputSchema` tells your agent the *shape* of a call — the
argument names, their types, which are required. It never tells you:

- which enum value actually **works**,
- the **minimal** argument set that succeeds,
- what the result actually **looks like**,
- what a **wrong** call returns,
- whether the tool works at all on this machine.

So the agent burns round-trips discovering it. Every time. On every task.

Call-Anything is a small MCP server that hands your agent invocations that
have **already been executed successfully**, with the real recorded output
as evidence.

```jsonc
// what a schema tells you
{"mode": {"type": "string", "enum": ["fast", "safe", "strict", "debug", "audit", "raw"]}}

// what Call-Anything tells you
{
  "tool": "analyze_binary",
  "arguments": {"mode": "safe", "depth": "shallow"},
  "evidence": "{\"result\": {\"open\": true, \"capabilities\": [...]}}",
  "verified_by": "recorded",
  "verified_at": "2026-10-21",
  "server_version": "6.7.0"
}
```

## Why this is not another schema or a cookbook

A schema documents what a call **may** look like. A cookbook documents
what someone **wrote down**. This corpus only contains calls that were
**actually executed and produced a result** — and the CI gate enforces it.

An entry that claims `verified_by: recorded` but carries no evidence
**fails the build**:

```
$ python3 tools/build_calls.py --check
corpus OK: 12 entries
```

```
$ # forge an entry with no evidence
$ python3 tools/build_calls.py --check
CORPUS INVALID:
  - entry 'forged' claims verified_by='recorded' but carries no evidence
```

That test is in the suite (`test_forged_entry_fails_validation`). It is
the entire reason this project is worth more than a YAML file.

## The gap, measured

On REA's real toolset (210 tools, `rea` 6.7.0):

| | |
|---|---|
| Properties across all tools | 592 |
| Properties carrying an `example` | **0** (0.0%) |
| Properties carrying a `default` | 156 (26.4%) |
| Enum values an agent could pick wrong | **4,641** |
| Enum decision points | 608 |
| Tools needing ≥1 unguessable choice | **161 (76.7%)** |
| Tools callable with no arguments at all | 13 |

`call_profile` computes this for any tool schema, and is the fastest way
to decide whether hunting for a verified call is worth it.

## Install

```bash
git clone https://github.com/<you>/call-anything
cd call-anything

# any MCP client
claude mcp add call-anything -- python3 "$PWD/tools/mcp/call_mcp.py"
# or opencode / cursor / hermes: same command, stdio transport
```

Requirements: Python 3.9+, PyYAML. No model, no API key, no network.

## The tools (6, ~900 tokens of schema)

| tool | what it does |
|---|---|
| `find_call` | Find verified invocations by server / tool / tag / query |
| `get_call` | One call in full, with the complete evidence output |
| `call_profile` | How hard is this tool to call? Guessing cost, minimal arity |
| `list_tools_of` | Which tools of a server have a known-good invocation? |
| `record_call` | Add a call you just executed successfully |
| `corpus_stats` | Coverage: servers, tools, verified vs claimed |

## How a call gets into the corpus

Two ways, and one hard rule.

**1. By hand** — edit `data/calls.yaml`, then `python3 tools/build_calls.py`.

**2. By execution** — the seeder makes the call for real and records the
actual output:

```bash
python3 tools/seed_from_server.py \
  --server rea --cmd "node /path/to/rea/scripts/rea.mjs mcp" \
  --call list_unknowns --summary "List residual unknowns" --tags rea
```

**The rule:** an entry without execution evidence is a *claim*, not a
verified call. Claims are stored, labelled `verified_by: claimed`, and
excluded from `find_call` by default. The corpus never pretends.

## Write-back loop

```bash
# 1. did someone already solve this?
find_call(tool="analyze_binary", query="elf")

# 2. call the tool for real
# 3. it worked
record_call(server="rea", tool="analyze_binary",
            arguments={...}, evidence="<real output>", summary="...")
# 4. commit and open a pull request
```

The pull request is the review unit. The CI gate re-checks that the
entry carries evidence and does not duplicate an existing call, and
regenerates `CALLS.md`. A forged or duplicate entry does not merge.

## Layout

```
data/calls.yaml        the corpus -- the source of truth
CALLS.md               generated index (never edit by hand)
tools/call_core.py     corpus model, validation, search
tools/call_profile.py  schema -> guessing-cost analysis
tools/mcp/call_mcp.py  the MCP server (stdlib only)
tools/seed_from_server.py  record calls by really executing them
tools/build_calls.py   validate + generate + CI gate
playbook/              how to work with this
```

## License

MIT.
