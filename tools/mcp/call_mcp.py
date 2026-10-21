#!/usr/bin/env python3
"""Call-Anything MCP server.

Exposes the corpus of VERIFIED working tool invocations.

The problem it solves: an MCP inputSchema tells an agent the SHAPE of a
call but never which arguments actually WORK. The agent has to burn
round-trips discovering that. This server hands over calls that have
already been executed successfully, with the real output as evidence.

Design constraints (deliberate):
  * stdlib only, no MCP SDK, no network, no model, no API key
  * BYOK: the agent is whatever client is talking to us
  * small surface: the whole schema costs ~800 tokens
  * refuses to serve unverified claims as if they were verified

Protocol: JSON-RPC 2.0 over stdio, per the MCP stdio transport.
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))          # tools/mcp
_TOOLS = os.path.dirname(_HERE)                            # tools
REPO_ROOT = os.path.dirname(_TOOLS)                       # repo root

sys.path.insert(0, _TOOLS)

import call_core as core          # noqa: E402
from call_profile import profile_tool, summarize  # noqa: E402
CORPUS_PATH = os.environ.get(
    "CALL_ANYTHING_CORPUS", os.path.join(REPO_ROOT, "data", "calls.yaml")
)

SERVER_NAME = "call-anything"
SERVER_VERSION = "0.1.0"
PROTOCOL_VERSION = "2024-11-05"

TOOLS = [
    {
        "name": "find_call",
        "description": (
            "Find a verified working invocation of an MCP tool. Use this "
            "BEFORE calling an unfamiliar tool: it returns the exact "
            "arguments that were proven to work, plus the recorded output. "
            "Filters: server, tool, tag, query. Returns only calls that "
            "carry execution evidence."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "tool": {
                    "type": "string",
                    "description": "Tool name, e.g. 'inspect_binary_layout'.",
                },
                "server": {
                    "type": "string",
                    "description": "Server the tool belongs to, e.g. 'rea'.",
                },
                "tag": {
                    "type": "string",
                    "description": "Narrow by tag, e.g. 'elf' or 'setup'.",
                },
                "query": {
                    "type": "string",
                    "description": "Free-text search over summary and arguments.",
                },
                "include_unverified": {
                    "type": "boolean",
                    "description": (
                        "Also return claimed-but-unverified entries. Off by "
                        "default: unverified calls are not safe to copy."
                    ),
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_call",
        "description": (
            "Get one verified call by its id, including the full recorded "
            "evidence output."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {
                    "type": "string",
                    "description": "Entry id, e.g. 'rea-list_architectures'.",
                }
            },
            "required": ["id"],
        },
    },
    {
        "name": "call_profile",
        "description": (
            "Compute how hard a tool is to call from its schema alone: "
            "required args, enum surfaces, guessing cost, minimal arity, "
            "difficulty. Use this to decide whether to hunt for a verified "
            "call first."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "tool_schema": {
                    "type": "object",
                    "description": (
                        "A tools/list entry: {name, inputSchema}. Pass the "
                        "entry exactly as the server advertised it."
                    ),
                }
            },
            "required": ["tool_schema"],
        },
    },
    {
        "name": "list_tools_of",
        "description": (
            "List the tools a known server exposes, along with the number of "
            "verified calls known for each. Use to find which tools have "
            "a known-good invocation."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "server": {
                    "type": "string",
                    "description": "Server name, e.g. 'rea'.",
                }
            },
            "required": ["server"],
        },
    },
    {
        "name": "record_call",
        "description": (
            "Record a tool invocation that you have just executed "
            "successfully, so the next agent does not have to rediscover "
            "it. Pass the arguments you used and a short excerpt of the "
            "result as evidence. This writes a proposed entry; it does not "
            "open a pull request."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "server": {"type": "string", "description": "Server name."},
                "tool": {"type": "string", "description": "Tool name."},
                "arguments": {
                    "type": "object",
                    "description": "The exact arguments that worked.",
                },
                "evidence": {
                    "type": "string",
                    "description": "Excerpt of the real result that proves it worked.",
                },
                "summary": {
                    "type": "string",
                    "description": "One line: what this call does.",
                },
                "server_version": {
                    "type": "string",
                    "description": "Version of the server the call was made against.",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Free tags for filtering.",
                },
            },
            "required": ["server", "tool", "evidence", "summary"],
        },
    },
    {
        "name": "corpus_stats",
        "description": (
            "How much of the corpus is verified, how many servers and tools "
            "it covers, and how the entries are tagged."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
]


# --------------------------------------------------------------------------
# helpers


def _text_result(payload: dict, is_error: bool = False) -> dict:
    return {
        "content": [
            {"type": "text", "text": json.dumps(payload, indent=2, sort_keys=True)}
        ],
        "isError": is_error,
    }


def _load() -> list[core.CallEntry]:
    return core.load_corpus(CORPUS_PATH)


def _entry_brief(e: core.CallEntry) -> dict:
    d = e.to_dict()
    # Truncate evidence in list context; get_call returns it in full.
    ev = d.get("evidence") or ""
    if len(ev) > 400:
        d["evidence"] = ev[:400] + " ...(truncated, use get_call for full)"
    return d


# --------------------------------------------------------------------------
# tool implementations


def _t_find_call(args: dict) -> dict:
    entries = _load()
    inc_unverified = bool(args.get("include_unverified"))
    hits = core.search(
        entries,
        query=args.get("query", "") or "",
        server=args.get("server", "") or "",
        tool=args.get("tool", "") or "",
        tag=args.get("tag", "") or "",
        verified_only=not inc_unverified,
    )
    return {
        "count": len(hits),
        "verified_only": not inc_unverified,
        "calls": [_entry_brief(e) for e in hits[:25]],
    }


def _t_get_call(args: dict) -> dict:
    entries = _load()
    e = core.get_entry(entries, args.get("id", ""))
    if e is None:
        return {"error": f"no entry with id {args.get('id')!r}"}
    return e.to_dict()


def _t_call_profile(args: dict) -> dict:
    schema = args.get("tool_schema")
    if not isinstance(schema, dict):
        return {"error": "tool_schema must be an object"}
    try:
        p = profile_tool(schema)
    except Exception as exc:
        return {"error": f"could not profile tool: {exc}"}
    return p.to_dict()


def _t_list_tools_of(args: dict) -> dict:
    entries = _load()
    server = (args.get("server") or "").lower()
    by_tool: dict[str, dict] = {}
    for e in entries:
        if server and e.server.lower() != server:
            continue
        rec = by_tool.setdefault(
            e.tool,
            {"tool": e.tool, "verified_calls": 0, "claimed_calls": 0, "ids": []},
        )
        if e.is_verified:
            rec["verified_calls"] += 1
        else:
            rec["claimed_calls"] += 1
        rec["ids"].append(e.id)
    return {
        "server": args.get("server", ""),
        "tool_count": len(by_tool),
        "tools": sorted(by_tool.values(), key=lambda t: t["tool"]),
    }


def _t_record_call(args: dict) -> dict:
    entries = _load()
    server = args.get("server")
    tool = args.get("tool")
    evidence = args.get("evidence")
    summary = args.get("summary")
    arguments = args.get("arguments") or {}

    if not server or not tool:
        return {"error": "server and tool are required"}
    if not evidence:
        return {"error": "evidence is required: a call without a recorded "
                         "result is a claim, not a verified call"}
    if not summary:
        return {"error": "summary is required"}

    existing = core.find_by_call(entries, server, tool, arguments)
    if existing is not None:
        return {
            "ok": False,
            "error": f"this exact call is already recorded as {existing.id!r}",
            "existing": _entry_brief(existing),
        }

    eid = core.next_id(entries, server, tool)
    entry = core.CallEntry(
        id=eid,
        server=server,
        tool=tool,
        arguments=arguments,
        summary=summary,
        evidence=evidence,
        verified_by="recorded",
        verified_at=_today(),
        server_version=args.get("server_version", "") or "",
        tags=list(args.get("tags") or []),
    )
    entries.append(entry)
    _write_corpus(entries)
    return {
        "ok": True,
        "id": eid,
        "call_id": entry.call_id,
        "entry": _entry_brief(entry),
        "note": "written locally. Commit and open a pull request to share it.",
    }


def _t_corpus_stats(args: dict) -> dict:
    entries = _load()
    verified = [e for e in entries if e.is_verified]
    servers: dict[str, int] = {}
    tools: set[str] = set()
    tags: dict[str, int] = {}
    for e in entries:
        servers[e.server] = servers.get(e.server, 0) + 1
        tools.add(f"{e.server}.{e.tool}")
        for t in e.tags:
            tags[t] = tags.get(t, 0) + 1
    return {
        "total_entries": len(entries),
        "verified_entries": len(verified),
        "claimed_entries": len(entries) - len(verified),
        "servers": servers,
        "distinct_tools": len(tools),
        "tags": tags,
        "verified_by": {
            vb: sum(1 for e in entries if e.verified_by == vb)
            for vb in core.VERIFIED_BY
        },
    }


def _today() -> str:
    import datetime
    return datetime.date.today().isoformat()


def _write_corpus(entries: list[core.CallEntry]) -> None:
    import yaml
    doc = {
        "version": 1,
        "calls": [e.to_dict() for e in entries],
    }
    # strip derived fields on write
    for c in doc["calls"]:
        c.pop("call_id", None)
        c.pop("is_verified", None)
    with open(CORPUS_PATH, "w", encoding="utf-8") as fh:
        yaml.safe_dump(doc, fh, sort_keys=False, allow_unicode=True,
                       default_flow_style=False, width=100)


HANDLERS = {
    "find_call": _t_find_call,
    "get_call": _t_get_call,
    "call_profile": _t_call_profile,
    "list_tools_of": _t_list_tools_of,
    "record_call": _t_record_call,
    "corpus_stats": _t_corpus_stats,
}


# --------------------------------------------------------------------------
# JSON-RPC plumbing


def _resp(req_id, result=None, error=None) -> dict:
    out = {"jsonrpc": "2.0", "id": req_id}
    if error is not None:
        out["error"] = error
    else:
        out["result"] = result
    return out


def handle(req: dict) -> dict | None:
    method = req.get("method")
    req_id = req.get("id")
    params = req.get("params") or {}

    if method == "initialize":
        return _resp(req_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": (
                "Call-Anything holds VERIFIED working invocations of MCP "
                "tools. Before calling an unfamiliar or schema-ambiguous "
                "tool, use find_call to get arguments that were proven to "
                "work. After a call succeeds, use record_call so the next "
                "agent can skip the discovery."
            ),
        })

    if method in ("notifications/initialized", "notifications/cancelled"):
        return None

    if method == "ping":
        return _resp(req_id, {})

    if method == "tools/list":
        return _resp(req_id, {"tools": TOOLS})

    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments")
        if not isinstance(args, dict):
            args = {}
        handler = HANDLERS.get(name)
        if handler is None:
            return _resp(req_id, result=_text_result(
                {"error": f"unknown tool {name!r}",
                 "available": sorted(HANDLERS)}, is_error=True))
        # enforce required args
        schema = next((t for t in TOOLS if t["name"] == name), None)
        missing = []
        if schema:
            for req_arg in schema["inputSchema"].get("required", []):
                if req_arg not in args:
                    missing.append(req_arg)
        if missing:
            return _resp(req_id, result=_text_result(
                {"error": f"missing required argument(s): {', '.join(missing)}",
                 "tool": name}, is_error=True))
        try:
            payload = handler(args)
        except core.CorpusError as exc:
            return _resp(req_id, result=_text_result(
                {"error": str(exc), "tool": name}, is_error=True))
        except Exception as exc:
            return _resp(req_id, result=_text_result(
                {"error": f"{type(exc).__name__}: {exc}", "tool": name},
                is_error=True))
        is_err = bool(isinstance(payload, dict) and payload.get("error"))
        return _resp(req_id, result=_text_result(payload, is_error=is_err))

    if method == "resources/list":
        return _resp(req_id, {"resources": []})

    if method == "prompts/list":
        return _resp(req_id, {"prompts": []})

    return _resp(req_id, error={
        "code": -32601,
        "message": f"method not found: {method}",
    })


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            sys.stdout.write(json.dumps(_resp(None, error={
                "code": -32700, "message": "parse error"})) + "\n")
            sys.stdout.flush()
            continue
        resp = handle(req)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
