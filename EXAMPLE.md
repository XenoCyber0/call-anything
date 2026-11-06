# Call-Anything: Practical Example

## The problem it solves

An MCP server declares its tools in `tools/list`. The schema tells you:
- what arguments a tool takes (names, types, required/optional)
- what enums are available
- what the defaults are

But the schema **never tells you**:
- which enum value actually works
- whether the tool needs a prerequisite step
- what the real output looks like

So an agent wastes round-trips guessing. Call-Anything hands it calls that were
**already proven to work**, with real output as evidence.

---

## Setup (one-time)

Register the corpus as an MCP server in your agent config:

```bash
cd /path/to/call-anything
pip install pyyaml   # only dependency
```

For Claude Code (`~/.claude/mcp.json`):
```json
{
  "mcpServers": {
    "call-anything": {
      "command": "python",
      "args": ["/path/to/call-anything/tools/mcp/call_mcp.py"]
    }
  }
}
```

For Opencode (`~/.config/opencode/opencode.json`):
```json
{
  "mcpServers": {
    "call-anything": {
      "command": "python",
      "args": ["/path/to/call-anything/tools/mcp/call_mcp.py"]
    }
  }
}
```

Restart the agent after adding. It will see 6 new tools.

---

## Scenario: You want to use the `rea` reverse-engineering server

The `rea` server has 210 tools. Most need specific arguments or prerequisites.
The corpus already has some verified calls:

| tool | works bare? | notes |
|------|------------|-------|
| `list_unknowns` | yes | empty args |
| `list_frida_devices` | yes | empty args |
| `current_address` | no | needs `open_binary` first |
| `list_documents` | no | needs `open_binary` first |

### Step 1: Look up a verified call before you call the tool

```
find_call(tool="list_unknowns")
```

Returns:
```json
{
  "count": 1,
  "calls": [{
    "id": "rea-list-unknowns",
    "server": "rea",
    "tool": "list_unknowns",
    "arguments": {},
    "summary": "Call list_unknowns with no arguments; works with no open target",
    "evidence": "{\"result\":{\"items\":[],\"total\":0,...}}",
    "verified_by": "recorded",
    "verified_at": "2026-10-21",
    "tags": ["rea", "no-args"]
  }]
}
```

You now know: call it with `{}`, it returns `{"result":{"items":[],"total":0,...}}`.
No guessing needed.

### Step 2: Look up a tool that needs prerequisites

```
find_call(tool="current_address")
```

Returns a precondition entry:
```json
{
  "id": "rea-current-address",
  "tool": "current_address",
  "arguments": {},
  "evidence": "{\"error\":{\"code\":\"target_unavailable\",\"message\":\"No app is open.\"}}",
  "tags": ["rea", "precondition"]
}
```

This tells you: calling this bare fails with `target_unavailable`.
You need to call `open_binary` first.

### Step 3: Check coverage before working with a new server

```
list_tools_of(server="rea")
```

Returns which of the 210 tools have verified calls and which don't.
Tells you where you need to do discovery vs where you can skip.

### Step 4: Profile a tool from its schema

You get a tool schema from `tools/list` but don't know if it's hard to call:

```
call_profile(tool_schema={
  "name": "analyze_binary",
  "inputSchema": {
    "type": "object",
    "properties": {
      "mode": {"type": "string", "enum": ["fast","safe","strict","debug","audit","raw"]},
      "depth": {"type": "string", "enum": ["shallow","deep","full"]},
      "path": {"type": "string"}
    },
    "required": ["path"]
  }
})
```

Returns:
```json
{
  "required": ["path"],
  "optional": ["mode", "depth"],
  "enum_count": 8,
  "unguessable": true,
  "difficulty": "high"
}
```

Tells you: 8 enum values to guess, hard to call without a verified example.
So you call `find_call(tool="analyze_binary")` first.

### Step 5: Record a new verified call after using a tool

You find a new tool that works and want to help future agents:

```
record_call(
  server="rea",
  tool="batch_decompile",
  arguments={},
  evidence="{\"result\":{\"items\":[],\"total\":0}}",
  summary="Batch decompile all loaded symbols",
  server_version="6.7.0",
  tags=["rea","decompile"]
)
```

This writes to `data/calls.yaml` locally. The next agent will find this
in `find_call`.

### Step 6: Seed the corpus from a real server

If you have the `rea` server installed, you can auto-discover working calls:

```bash
python tools/seed_from_server.py \
  --server rea \
  --cmd "node /path/to/rea/scripts/rea.mjs mcp" \
  --call list_unknowns \
  --args '{}' \
  --summary "List residual unknowns" \
  --tags "rea"
```

This launches the server, calls `list_unknowns`, captures the real output,
and adds it to the corpus if it succeeded.

---

## The write-back loop in one picture

```
1. find_call(tool="analyze_binary")       # check if someone already solved this
2. [if found, use the recorded args]
3. [if not, call the tool with schema hints]
4. It works!
5. record_call(server="rea", tool="analyze_binary", ...)   # save for next time
6. Commit the change, open a PR so everyone benefits
```

No model calls. No API keys. No network. Just real results.
