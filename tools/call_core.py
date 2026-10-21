"""Call-Anything: the corpus of VERIFIED working tool invocations.

The difference from a schema, a doc, or a cookbook:

    A schema says what a call MAY look like.
    This corpus says which call ACTUALLY WORKS, on which server version,
    and carries the real recorded output as evidence.

Every entry in data/calls.yaml must have:
    server, tool, arguments
    evidence: the actual result (or a digest of it)
    verified: how it was confirmed to work
    verified_at: when

An entry with no evidence is a CLAIM, not a verified call. The loader
separates the two, and the MCP server refuses to serve claims as if they
were verified.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - documented optional dep
    yaml = None

DATA_FILE = "data/calls.yaml"

# How an invocation was proven to work.
VERIFIED_BY = {
    "recorded": "the call was executed and returned a successful result",
    "replayed": "the call was executed again later and still succeeded",
    "witnessed": "a human ran it and confirmed the outcome",
    "claimed": "asserted, with no execution evidence (NOT verified)",
}


@dataclass
class CallEntry:
    """One verified invocation of one tool."""

    id: str
    server: str
    tool: str
    arguments: dict[str, Any] = field(default_factory=dict)
    summary: str = ""
    evidence: str = ""
    verified_by: str = "recorded"
    verified_at: str = ""
    server_version: str = ""
    tags: list[str] = field(default_factory=list)
    notes: str = ""

    @property
    def is_verified(self) -> bool:
        return self.verified_by != "claimed" and bool(self.evidence)

    @property
    def call_id(self) -> str:
        """Stable identity of the CALL itself (server+tool+args)."""
        payload = json.dumps(
            {
                "server": self.server,
                "tool": self.tool,
                "arguments": self.arguments,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:12]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "server": self.server,
            "tool": self.tool,
            "arguments": self.arguments,
            "summary": self.summary,
            "evidence": self.evidence,
            "verified_by": self.verified_by,
            "verified_at": self.verified_at,
            "server_version": self.server_version,
            "tags": self.tags,
            "notes": self.notes,
            "call_id": self.call_id,
            "is_verified": self.is_verified,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CallEntry":
        return cls(
            id=d.get("id", ""),
            server=d.get("server", ""),
            tool=d.get("tool", ""),
            arguments=d.get("arguments") or {},
            summary=d.get("summary", ""),
            evidence=d.get("evidence", ""),
            verified_by=d.get("verified_by", "claimed"),
            verified_at=str(d.get("verified_at", "")),
            server_version=d.get("server_version", ""),
            tags=list(d.get("tags") or []),
            notes=d.get("notes", ""),
        )


class CorpusError(Exception):
    pass


def _require_yaml():
    if yaml is None:
        raise CorpusError(
            "PyYAML is required to read the corpus (pip install pyyaml)"
        )
    return yaml


def load_corpus(path: str = DATA_FILE) -> list[CallEntry]:
    """Load and validate the corpus. Raises CorpusError on invalid data."""
    yml = _require_yaml()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = yml.safe_load(fh)
    except FileNotFoundError:
        raise CorpusError(f"corpus not found at {path}")
    except Exception as exc:
        raise CorpusError(f"could not parse {path}: {exc}")

    if raw is None:
        return []
    if not isinstance(raw, dict) or "calls" not in raw:
        raise CorpusError("corpus must be a mapping with a 'calls' key")
    items = raw.get("calls")
    if not isinstance(items, list):
        raise CorpusError("corpus 'calls' must be a list")

    entries = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise CorpusError(f"entry #{i} is not a mapping")
        e = CallEntry.from_dict(item)
        if not e.id:
            raise CorpusError(f"entry #{i} has no id")
        if not e.server:
            raise CorpusError(f"entry {e.id!r} has no server")
        if not e.tool:
            raise CorpusError(f"entry {e.id!r} has no tool")
        if e.verified_by not in VERIFIED_BY:
            raise CorpusError(
                f"entry {e.id!r} has unknown verified_by "
                f"{e.verified_by!r} (expected one of {sorted(VERIFIED_BY)})"
            )
        entries.append(e)
    return entries


def validate_corpus(path: str = DATA_FILE) -> tuple[bool, list[str]]:
    """Validate the corpus. Returns (ok, errors)."""
    try:
        entries = load_corpus(path)
    except CorpusError as exc:
        return False, [str(exc)]

    errors: list[str] = []
    seen_ids: set[str] = set()
    seen_calls: dict[str, str] = {}

    for e in entries:
        if e.id in seen_ids:
            errors.append(f"duplicate id {e.id!r}")
        seen_ids.add(e.id)

        if e.verified_by != "claimed" and not e.evidence:
            errors.append(
                f"entry {e.id!r} claims verified_by={e.verified_by!r} "
                f"but carries no evidence"
            )
        if e.is_verified and not e.verified_at:
            errors.append(
                f"entry {e.id!r} is verified but has no verified_at date"
            )
        if e.verified_by == "claimed" and e.evidence:
            errors.append(
                f"entry {e.id!r} is marked 'claimed' but carries evidence; "
                f"it should be recorded"
            )
        if e.verified_by not in ("claimed",) and e.server_version == "" and e.verified_by == "replayed":
            errors.append(
                f"entry {e.id!r} was replayed but records no server_version"
            )

        prev = seen_calls.get(e.call_id)
        if prev:
            errors.append(
                f"entry {e.id!r} duplicates the call of {prev!r} "
                f"(same server+tool+arguments)"
            )
        else:
            seen_calls[e.call_id] = e.id

    return (not errors), errors


def search(
    entries: list[CallEntry],
    query: str = "",
    server: str = "",
    tool: str = "",
    tag: str = "",
    verified_only: bool = True,
) -> list[CallEntry]:
    """Search the corpus. All filters are ANDed; a blank filter is ignored."""
    q = query.strip().lower()
    out = []
    for e in entries:
        if verified_only and not e.is_verified:
            continue
        if server and e.server.lower() != server.lower():
            continue
        if tool and e.tool.lower() != tool.lower():
            continue
        if tag and tag.lower() not in [t.lower() for t in e.tags]:
            continue
        if q:
            hay = " ".join(
                [
                    e.id,
                    e.server,
                    e.tool,
                    e.summary,
                    e.notes,
                    " ".join(e.tags),
                    json.dumps(e.arguments, sort_keys=True),
                ]
            ).lower()
            if q not in hay:
                continue
        out.append(e)
    return out


def get_entry(entries: list[CallEntry], entry_id: str) -> CallEntry | None:
    for e in entries:
        if e.id == entry_id:
            return e
    return None


def find_by_call(
    entries: list[CallEntry],
    server: str,
    tool: str,
    arguments: dict[str, Any],
) -> CallEntry | None:
    """Find the entry for an exact (server, tool, arguments) triple."""
    payload = json.dumps(
        {"server": server, "tool": tool, "arguments": arguments},
        sort_keys=True,
        separators=(",", ":"),
    )
    cid = hashlib.sha256(payload.encode()).hexdigest()[:12]
    for e in entries:
        if e.call_id == cid:
            return e
    return None


def next_id(entries: list[CallEntry], server: str, tool: str) -> str:
    """Deterministic id for a new entry."""
    slug = re.sub(r"[^a-z0-9]+", "-", f"{server}-{tool}".lower()).strip("-")
    existing = {e.id for e in entries}
    if slug not in existing:
        return slug
    i = 2
    while f"{slug}-{i}" in existing:
        i += 1
    return f"{slug}-{i}"
