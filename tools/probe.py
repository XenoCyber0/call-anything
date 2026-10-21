"""Probe a live MCP server and discover calls that actually work.

This is the part that changes the product. Instead of asking people to
write down working invocations, we EXECUTE the server's own tools and
record what worked.

The discovery strategy is deliberately boring and deterministic:

    1. Call every tool with NO arguments.
       Many tools are callable bare, or fail with a message that names
       the problem.
    2. For tools that need arguments, try each required argument with a
       small set of plausible values, taken from:
         - the schema's own enum values (if it has any)
         - the schema's own examples / defaults
         - a tiny built-in vocabulary of common values
    3. Keep the combinations that SUCCEED.
    4. Record the failures that reveal a PRECONDITION, because a failure
       that says "open the target first" is verified knowledge about
       the order of operations.

Nothing here uses a model. It is a bounded search over a server's own
declared interface, and every result it keeps was actually executed.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from call_profile import profile_tool

# Common values an agent would plausibly try, per type. Deliberately tiny:
# this is a probe, not a fuzzer.
COMMON_VALUES: dict[str, list[Any]] = {
    "string": ["example", "test", "default", "main", "app", "sample", "1"],
    "integer": [0, 1],
    "number": [0, 1],
    "boolean": [True, False],
}

# Strings that are almost never valid but are worth one try because they
# are what a naive agent reaches for.
NEVER_USEFUL = {"", "null", "undefined", "none", "todo", "changeme", "xxx"}


@dataclass
class Probe:
    """One attempted invocation."""

    tool: str
    arguments: dict[str, Any]
    ok: bool
    output: str = ""
    error_code: str = ""
    error_message: str = ""
    raw_output: str = ""


@dataclass
class Finding:
    """What the probe learned about one tool."""

    tool: str
    working: list[dict[str, Any]] = field(default_factory=list)
    working_evidence: list[str] = field(default_factory=list)
    preconditions: list[str] = field(default_factory=list)
    requires_args: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    # The RAW error payloads, preserved verbatim so the replay verifier can
    # compare them shape-for-shape. A summarised error is not evidence.
    raw_errors: list[str] = field(default_factory=list)
    # The arguments that produced the precondition, so the entry replays
    # exactly.
    precondition_args: dict[str, Any] = field(default_factory=dict)

    @property
    def is_callable_bare(self) -> bool:
        """True when the tool was proven to work with no arguments."""
        return bool(self.working) and not self.requires_args


def _candidates(prop: dict[str, Any]) -> list[Any]:
    """Values worth trying for one property, best-first."""
    out: list[Any] = []

    if "const" in prop:
        out.append(prop["const"])
    if "default" in prop:
        out.append(prop["default"])
    for key in ("examples", "example"):
        v = prop.get(key)
        if isinstance(v, list):
            out.extend(v)
        elif v is not None:
            out.append(v)
    if isinstance(prop.get("enum"), list):
        out.extend(prop["enum"])

    t = prop.get("type")
    if isinstance(t, list):
        t = t[0] if t else "string"
    if isinstance(t, str) and t in COMMON_VALUES:
        out.extend(COMMON_VALUES[t])

    # De-dup, drop useless values, keep order.
    seen: set[str] = set()
    clean: list[Any] = []
    for v in out:
        if isinstance(v, str) and v.strip().lower() in NEVER_USEFUL:
            continue
        k = json.dumps(v, sort_keys=True)
        if k in seen:
            continue
        seen.add(k)
        clean.append(v)
    return clean


def _error_parts(result: dict[str, Any]) -> tuple[str, str]:
    """Extract (code, message) from an MCP error result."""
    text = ""
    for c in result.get("content") or []:
        if c.get("type") == "text":
            text += c.get("text", "")
    if not text:
        return "", ""
    try:
        obj = json.loads(text)
    except Exception:
        m = re.search(r"(\w+)\s*error", text, re.I)
        return (m.group(1).lower() if m else ""), text.strip()[:300]
    err = obj.get("error") if isinstance(obj, dict) else None
    if isinstance(err, dict):
        return str(err.get("code", "")), str(err.get("message", ""))[:300]
    return "", text.strip()[:300]


def _looks_like_precondition(code: str, message: str) -> bool:
    """Does this failure mean 'do something else first'?"""
    blob = f"{code} {message}".lower()
    needles = (
        "not_open", "no app is open", "target_unavailable", "no target",
        "not loaded", "no session", "requires", "must be opened",
        "open the", "no binary", "unavailable", "not initialized",
        "session_unavailable", "not_connected",
    )
    return any(n in blob for n in needles)


def _looks_like_missing_arg(code: str, message: str) -> bool:
    blob = f"{code} {message}".lower()
    return any(
        n in blob for n in (
            "missing", "required", "invalid arguments", "must be",
            "validation", "expected", "parameter",
        )
    )


def plan_probes(tool: dict[str, Any], max_per_tool: int = 12) -> list[dict[str, Any]]:
    """Build the list of argument combinations to try for one tool.

    Deterministic and bounded. Order matters: the cheapest, most likely
    combination is tried first.
    """
    schema = tool.get("inputSchema") or {}
    props = schema.get("properties") or {}
    required = list(schema.get("required") or [])

    combos: list[dict[str, Any]] = [{}]

    if required:
        # Try each required arg alone first (isolates which one matters),
        # then the full set.
        for rname in required:
            rprop = props.get(rname) or {}
            for val in _candidates(rprop)[:3]:
                combos.append({rname: val})
        # The full set, first candidate of each.
        full = {}
        for rname in required:
            rprop = props.get(rname) or {}
            cands = _candidates(rprop)
            if cands:
                full[rname] = cands[0]
        if full:
            combos.append(full)

    # Optional args: try adding one at a time to the bare call.
    optional = [p for p in props if p not in required]
    for pname in optional[:4]:
        for val in _candidates(props.get(pname) or {})[:2]:
            combos.append({pname: val})

    # De-dup, preserving order, and cap the total.
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for c in combos:
        k = json.dumps(c, sort_keys=True)
        if k in seen:
            continue
        seen.add(k)
        out.append(c)
        if len(out) >= max_per_tool:
            break
    return out


def interpret(tool_name: str, response: dict[str, Any]) -> tuple[bool, str, str, str]:
    """Turn an MCP tools/call response into (ok, output, code, message).

    Accepts either the raw JSON-RPC envelope ({"result": {...}}) or the
    tools/call result object itself.
    """
    result = response
    if isinstance(response, dict) and "result" in response and \
            "content" not in response:
        result = response.get("result") or {}
    if not isinstance(result, dict):
        result = {}
    if result.get("isError"):
        code, msg = _error_parts(result)
        return False, "", code, msg
    texts = [
        c.get("text", "") for c in result.get("content") or []
        if c.get("type") == "text"
    ]
    out = "\n".join(texts)
    # A result that is JSON containing an "error" key is also a failure,
    # even when isError is false -- some servers get this wrong.
    try:
        obj = json.loads(out)
        if isinstance(obj, dict) and "error" in obj:
            err = obj["error"]
            if isinstance(err, dict):
                return False, "", str(err.get("code", "")), str(err.get("message", ""))[:300]
    except Exception:
        pass
    if not out.strip():
        return False, "", "empty", "tool returned no content"
    return True, out, "", ""


def summarise_probe(tool_name: str, args: dict[str, Any],
                    response: dict[str, Any]) -> Probe:
    ok, out, code, msg = interpret(tool_name, response)
    raw = ""
    if not ok:
        result = response
        if isinstance(response, dict) and "result" in response and \
                "content" not in response:
            result = response.get("result") or {}
        raw = "".join(
            c.get("text", "") for c in (result or {}).get("content", [])
            if c.get("type") == "text"
        )
        if not raw.strip():
            raw = json.dumps({"error": {"code": code, "message": msg}})
    return Probe(tool=tool_name, arguments=args, ok=ok, output=out,
                 error_code=code, error_message=msg, raw_output=raw)


def classify_failure(p: Probe) -> str:
    """What kind of failure was this?"""
    if p.ok:
        return "ok"
    if _looks_like_precondition(p.error_code, p.error_message):
        return "precondition"
    if _looks_like_missing_arg(p.error_code, p.error_message):
        return "needs_args"
    return "other"


def describe(tool: dict[str, Any]) -> str:
    """One-line human description of a tool, from its own schema."""
    desc = (tool.get("description") or "").strip()
    first = desc.split("\n")[0].strip() if desc else ""
    p = profile_tool(tool)
    bits = []
    if p.required:
        bits.append("requires " + ", ".join(p.required))
    else:
        bits.append("no required args")
    if p.total_guessing_cost:
        bits.append(f"{p.total_guessing_cost} enum values to guess")
    tail = f"[{'; '.join(bits)}]"
    if first:
        return f"{first} {tail}"
    return tail
