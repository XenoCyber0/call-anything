"""The one thing an MCP schema cannot tell you.

An inputSchema documents the SHAPE of a call:
    what the arguments are called, what type they are, what is required.

It does not tell you:
    WHICH enum value actually works,
    the MINIMAL argument set that succeeds,
    what the result LOOKS like,
    what a WRONG call returns,
    whether the tool works AT ALL on this machine.

That gap is measurable: across REA's 210 tools there are 592 properties
and ZERO of them carry an example; 4,641 enum values exist; 161 tools
(77%) require at least one choice a schema cannot communicate.

This module computes a CALL GUIDANCE PROFILE for a tool schema:
    - required-ness vs optionality
    - enum surfaces and their guessing cost
    - which arguments have a usable default
    - a minimal-arity guess
    - whether a call can be made with no arguments at all

It is deterministic, model-free, and needs no network.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

# A schema is "communicable" for an argument if it pins the value down:
# it has a default, an example, a const, or a small closed enum.
COMMUNICABLE_ENUM_MAX = 4


@dataclass
class EnumSurface:
    """One enum-valued choice the agent has to make."""

    arg: str
    values: list[str]
    communicable: bool          # has default/example/const, or tiny enum
    default: str | None = None

    @property
    def guessing_cost(self) -> int:
        """How many wrong answers are possible."""
        if self.communicable:
            return 0
        return max(0, len(self.values))


@dataclass
class CallProfile:
    """What the schema says, translated into what an agent must guess."""

    tool: str
    required: list[str] = field(default_factory=list)
    optional: list[str] = field(default_factory=list)
    defaults: dict[str, Any] = field(default_factory=dict)
    enums: list[EnumSurface] = field(default_factory=list)
    free_text_required: list[str] = field(default_factory=list)
    nested_objects: list[str] = field(default_factory=list)

    # -- derived ---------------------------------------------------------
    @property
    def min_arity(self) -> int:
        """Fewest arguments that could possibly form a valid call."""
        return len(self.required)

    @property
    def callable_bare(self) -> bool:
        return not self.required

    @property
    def total_guessing_cost(self) -> int:
        return sum(e.guessing_cost for e in self.enums)

    @property
    def unguessable(self) -> bool:
        """True when the schema alone cannot determine a working call."""
        return self.total_guessing_cost > 0 or bool(self.free_text_required)

    def difficulty(self) -> str:
        cost = self.total_guessing_cost
        if not self.required and cost == 0:
            return "trivial"
        if cost == 0 and not self.free_text_required:
            return "easy"
        if cost <= 3 and not self.free_text_required:
            return "moderate"
        return "hard"

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "required": self.required,
            "optional": self.optional,
            "defaults": self.defaults,
            "enums": [
                {
                    "arg": e.arg,
                    "values": e.values,
                    "communicable": e.communicable,
                    "default": e.default,
                }
                for e in self.enums
            ],
            "free_text_required": self.free_text_required,
            "nested_objects": self.nested_objects,
            "min_arity": self.min_arity,
            "callable_bare": self.callable_bare,
            "total_guessing_cost": self.total_guessing_cost,
            "unguessable": self.unguessable,
            "difficulty": self.difficulty(),
        }


def _enum_values(node: dict[str, Any]) -> list[str]:
    vals = node.get("enum")
    if not isinstance(vals, list):
        return []
    out = []
    for v in vals:
        if isinstance(v, (str, int, float, bool)) or v is None:
            out.append(str(v))
        else:
            out.append(json.dumps(v, sort_keys=True))
    return out


def _collect_enums(
    schema: dict[str, Any], arg: str, out: list[EnumSurface]
) -> None:
    """Walk a property schema, finding every enum surface underneath it."""
    if not isinstance(schema, dict):
        return

    if isinstance(schema.get("enum"), list):
        vals = _enum_values(schema)
        default = schema.get("default")
        communicable = (
            "example" in schema
            or "examples" in schema
            or default is not None
            or "const" in schema
            or len(vals) <= COMMUNICABLE_ENUM_MAX
        )
        out.append(
            EnumSurface(
                arg=arg,
                values=vals,
                communicable=communicable,
                default=str(default) if default is not None else None,
            )
        )

    for key in ("oneOf", "anyOf", "allOf"):
        for sub in schema.get(key) or []:
            if isinstance(sub, dict):
                _collect_enums(sub, arg, out)

    props = schema.get("properties")
    if isinstance(props, dict):
        for pname, pschema in props.items():
            _collect_enums(pschema, f"{arg}.{pname}" if arg else pname, out)


def profile_tool(tool: dict[str, Any]) -> CallProfile:
    """Build a CallProfile from an MCP tools/list entry."""
    name = tool.get("name") or "?"
    schema = tool.get("inputSchema") or {}
    if not isinstance(schema, dict):
        schema = {}
    props = schema.get("properties")
    props = props if isinstance(props, dict) else {}
    required = [r for r in (schema.get("required") or []) if isinstance(r, str)]

    p = CallProfile(tool=name)

    for rname in required:
        p.required.append(rname)
    for pname in props:
        if pname not in required:
            p.optional.append(pname)

    for pname, pschema in props.items():
        if not isinstance(pschema, dict):
            continue
        if "default" in pschema:
            p.defaults[pname] = pschema["default"]
        if pschema.get("type") == "object" and "properties" in pschema:
            p.nested_objects.append(pname)
        if (
            pname in required
            and pschema.get("type") == "string"
            and "enum" not in pschema
            and "const" not in pschema
            and "default" not in pschema
            and "example" not in pschema
        ):
            p.free_text_required.append(pname)

    for pname, pschema in props.items():
        _collect_enums(pschema, pname, p.enums)

    return p


def profile_tools(tools: list[dict[str, Any]]) -> list[CallProfile]:
    return [profile_tool(t) for t in tools]


def summarize(profiles: list[CallProfile]) -> dict[str, Any]:
    """Corpus-level statistics about how guessable a toolset is."""
    n = len(profiles)
    if n == 0:
        return {"tools": 0}
    return {
        "tools": n,
        "callable_bare": sum(1 for p in profiles if p.callable_bare),
        "unguessable": sum(1 for p in profiles if p.unguessable),
        "total_enum_values": sum(p.total_guessing_cost for p in profiles),
        "total_enum_surfaces": sum(len(p.enums) for p in profiles),
        "free_text_required": sum(len(p.free_text_required) for p in profiles),
        "difficulty": {
            d: sum(1 for p in profiles if p.difficulty() == d)
            for d in ("trivial", "easy", "moderate", "hard")
        },
    }
