#!/usr/bin/env python3
"""Tests for Call-Anything.

The most important test in this file is test_forged_entry_fails_validation:
it proves the gate actually rejects an entry that claims verification
without evidence. Without that, the project is just a YAML file.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools"))

import call_core as core          # noqa: E402
from call_profile import profile_tool, summarize  # noqa: E402

CORPUS = os.path.join(REPO, "data", "calls.yaml")
MCP = os.path.join(REPO, "tools", "mcp", "call_mcp.py")


# --------------------------------------------------------------------------
# helpers


def make_corpus(entries: list[dict]) -> str:
    import yaml
    fd, path = tempfile.mkstemp(suffix=".yaml")
    with os.fdopen(fd, "w") as fh:
        yaml.safe_dump({"version": 1, "calls": entries}, fh)
    return path


def rpc(method: str, params: dict | None = None, req_id: int = 1) -> dict:
    """Send one JSON-RPC request to the MCP server and read the reply."""
    p = subprocess.Popen(
        [sys.executable, MCP],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True, bufsize=1,
    )
    req = {"jsonrpc": "2.0", "id": req_id, "method": method,
           "params": params or {}}
    p.stdin.write(json.dumps(req) + "\n")
    p.stdin.flush()
    reply = None
    while True:
        line = p.stdout.readline()
        if not line:
            break
        try:
            reply = json.loads(line)
            break
        except json.JSONDecodeError:
            continue
    p.terminate()
    p.wait(timeout=5)
    for stream in (p.stdin, p.stdout):
        try:
            if stream:
                stream.close()
        except Exception:
            pass
    return reply


def payload_of(reply: dict) -> dict:
    res = reply["result"]
    text = "".join(
        c.get("text", "") for c in res.get("content", []) if c.get("type") == "text"
    )
    return json.loads(text)


# --------------------------------------------------------------------------
# corpus integrity


class TestCorpusIntegrity(unittest.TestCase):
    def test_shipped_corpus_is_valid(self):
        ok, errors = core.validate_corpus(CORPUS)
        self.assertTrue(ok, f"shipped corpus invalid: {errors}")

    def test_forged_entry_fails_validation(self):
        """An entry claiming to be recorded, with no evidence, must fail."""
        path = make_corpus([{
            "id": "forged",
            "server": "rea",
            "tool": "list_documents",
            "arguments": {},
            "summary": "totally works",
            "evidence": "",              # <-- no proof
            "verified_by": "recorded",  # <-- but claims it does
            "verified_at": "2026-10-21",
        }])
        try:
            ok, errors = core.validate_corpus(path)
            self.assertFalse(ok, "a forged entry passed validation")
            self.assertTrue(
                any("no evidence" in e for e in errors),
                f"expected an evidence error, got: {errors}",
            )
        finally:
            os.unlink(path)

    def test_claimed_entry_with_evidence_is_rejected(self):
        """If it has evidence, it is not 'claimed' -- one of the two is wrong."""
        path = make_corpus([{
            "id": "mismatch",
            "server": "rea",
            "tool": "t",
            "arguments": {},
            "summary": "s",
            "evidence": "actual output",
            "verified_by": "claimed",
            "verified_at": "",
        }])
        try:
            ok, errors = core.validate_corpus(path)
            self.assertFalse(ok)
            self.assertTrue(any("marked 'claimed'" in e for e in errors),
                            f"got: {errors}")
        finally:
            os.unlink(path)

    def test_duplicate_call_is_rejected(self):
        entry = {
            "id": "a", "server": "rea", "tool": "t", "arguments": {"x": 1},
            "summary": "s", "evidence": "e", "verified_by": "recorded",
            "verified_at": "2026-10-21",
        }
        dup = dict(entry, id="b")
        path = make_corpus([entry, dup])
        try:
            ok, errors = core.validate_corpus(path)
            self.assertFalse(ok)
            self.assertTrue(any("duplicates the call" in e for e in errors),
                            f"got: {errors}")
        finally:
            os.unlink(path)

    def test_unknown_verified_by_is_rejected(self):
        path = make_corpus([{
            "id": "x", "server": "s", "tool": "t", "arguments": {},
            "summary": "s", "evidence": "e", "verified_by": "vibes",
            "verified_at": "2026-10-21",
        }])
        try:
            ok, errors = core.validate_corpus(path)
            self.assertFalse(ok)
            self.assertTrue(any("unknown verified_by" in e for e in errors),
                            f"got: {errors}")
        finally:
            os.unlink(path)

    def test_shipped_entries_all_carry_evidence(self):
        for e in core.load_corpus(CORPUS):
            self.assertTrue(
                e.evidence,
                f"shipped entry {e.id!r} has no evidence",
            )
            self.assertTrue(e.verified_at, f"{e.id!r} has no date")


# --------------------------------------------------------------------------
# search behaviour


class TestSearch(unittest.TestCase):
    def setUp(self):
        self.entries = core.load_corpus(CORPUS)

    def test_search_by_tool(self):
        hits = core.search(self.entries, tool="list_unknowns")
        self.assertTrue(hits)
        self.assertTrue(all(h.tool == "list_unknowns" for h in hits))

    def test_search_excludes_unverified_by_default(self):
        claimed = core.CallEntry(
            id="c", server="s", tool="t", arguments={}, summary="s",
            evidence="", verified_by="claimed", verified_at="",
        )
        hits = core.search(self.entries + [claimed], tool="t")
        self.assertNotIn("c", [h.id for h in hits])

    def test_search_includes_unverified_on_request(self):
        claimed = core.CallEntry(
            id="c", server="s", tool="t", arguments={}, summary="s",
            evidence="", verified_by="claimed", verified_at="",
        )
        hits = core.search(self.entries + [claimed], tool="t",
                           verified_only=False)
        self.assertIn("c", [h.id for h in hits])

    def test_filters_are_anded(self):
        hits = core.search(self.entries, server="rea", tool="list_unknowns")
        self.assertTrue(hits)
        self.assertTrue(all(h.server == "rea" for h in hits))
        self.assertEqual(core.search(self.entries, server="nope"), [])

    def test_call_id_is_order_independent(self):
        a = core.CallEntry(id="a", server="s", tool="t",
                           arguments={"x": 1, "y": 2}, summary="", evidence="e")
        b = core.CallEntry(id="b", server="s", tool="t",
                           arguments={"y": 2, "x": 1}, summary="", evidence="e")
        self.assertEqual(a.call_id, b.call_id)

    def test_call_id_differs_on_arguments(self):
        a = core.CallEntry(id="a", server="s", tool="t",
                           arguments={"x": 1}, summary="", evidence="e")
        b = core.CallEntry(id="b", server="s", tool="t",
                           arguments={"x": 2}, summary="", evidence="e")
        self.assertNotEqual(a.call_id, b.call_id)


# --------------------------------------------------------------------------
# call profiling


class TestCallProfile(unittest.TestCase):
    def test_no_required_args_is_callable_bare(self):
        p = profile_tool({
            "name": "t",
            "inputSchema": {"type": "object", "properties": {"a": {"type": "string"}}},
        })
        self.assertTrue(p.callable_bare)
        self.assertEqual(p.min_arity, 0)
        self.assertFalse(p.unguessable)

    def test_enum_without_example_is_unguessable(self):
        p = profile_tool({
            "name": "t",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "mode": {"type": "string",
                             "enum": ["a", "b", "c", "d", "e", "f"]}
                },
                "required": ["mode"],
            },
        })
        self.assertTrue(p.unguessable)
        self.assertEqual(p.total_guessing_cost, 6)

    def test_tiny_enum_is_communicable(self):
        p = profile_tool({
            "name": "t",
            "inputSchema": {
                "type": "object",
                "properties": {"mode": {"type": "string", "enum": ["a", "b"]}},
                "required": ["mode"],
            },
        })
        self.assertEqual(p.total_guessing_cost, 0)
        self.assertFalse(p.unguessable)

    def test_enum_with_default_is_communicable(self):
        p = profile_tool({
            "name": "t",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "enum": list("abcdef"),
                             "default": "a"}
                },
                "required": ["mode"],
            },
        })
        self.assertEqual(p.total_guessing_cost, 0)

    def test_free_text_required_string_counts(self):
        p = profile_tool({
            "name": "t",
            "inputSchema": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        })
        self.assertEqual(p.free_text_required, ["path"])
        self.assertTrue(p.unguessable)

    def test_nested_oneof_enum_found(self):
        p = profile_tool({
            "name": "t",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "target": {
                        "oneOf": [
                            {"enum": ["x", "y", "z", "w", "v", "u"]},
                            {"type": "object"},
                        ]
                    }
                },
                "required": ["target"],
            },
        })
        self.assertEqual(p.total_guessing_cost, 6)

    def test_difficulty_buckets(self):
        easy = profile_tool({"name": "t", "inputSchema": {"type": "object",
                                                          "properties": {}}})
        self.assertEqual(easy.difficulty(), "trivial")
        hard = profile_tool({
            "name": "t",
            "inputSchema": {
                "type": "object",
                "properties": {"m": {"type": "string",
                                     "enum": list("abcdefgh")}},
                "required": ["m"],
            },
        })
        self.assertEqual(hard.difficulty(), "hard")

    def test_summarize_counts(self):
        ps = [
            profile_tool({"name": "a", "inputSchema": {"type": "object",
                                                       "properties": {}}}),
            profile_tool({
                "name": "b",
                "inputSchema": {
                    "type": "object",
                    "properties": {"m": {"type": "string",
                                         "enum": list("abcdefgh")}},
                    "required": ["m"],
                },
            }),
        ]
        s = summarize(ps)
        self.assertEqual(s["tools"], 2)
        self.assertEqual(s["callable_bare"], 1)
        self.assertEqual(s["unguessable"], 1)
        self.assertEqual(s["total_enum_values"], 8)


# --------------------------------------------------------------------------
# MCP protocol


class TestMCPProtocol(unittest.TestCase):
    def test_initialize_reports_server(self):
        r = rpc("initialize", {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "t", "version": "1"},
        })
        self.assertEqual(r["result"]["serverInfo"]["name"], "call-anything")
        self.assertIn("instructions", r["result"])

    def test_tools_list_is_small(self):
        r = rpc("tools/list")
        tools = r["result"]["tools"]
        self.assertEqual(len(tools), 6)
        for t in tools:
            self.assertTrue(t["description"].strip(), f"{t['name']} has no desc")
            self.assertIn("inputSchema", t)

    def test_find_call_returns_verified_entries(self):
        r = rpc("tools/call", {"name": "find_call",
                               "arguments": {"tool": "list_unknowns"}})
        p = payload_of(r)
        self.assertGreaterEqual(p["count"], 1)
        for c in p["calls"]:
            self.assertTrue(c["is_verified"])
            self.assertTrue(c["evidence"])

    def test_find_call_hides_unverified_by_default(self):
        r = rpc("tools/call", {"name": "find_call",
                               "arguments": {"tool": "list_unknowns"}})
        p = payload_of(r)
        self.assertTrue(p["verified_only"])

    def test_missing_required_argument_is_an_error(self):
        r = rpc("tools/call", {"name": "get_call", "arguments": {}})
        self.assertTrue(r["result"]["isError"])
        p = payload_of(r)
        self.assertIn("missing required argument", p["error"])

    def test_unknown_tool_is_an_error_not_a_crash(self):
        r = rpc("tools/call", {"name": "nope", "arguments": {}})
        self.assertTrue(r["result"]["isError"])
        self.assertIn("unknown tool", payload_of(r)["error"])

    def test_call_profile_over_the_wire(self):
        r = rpc("tools/call", {
            "name": "call_profile",
            "arguments": {"tool_schema": {
                "name": "x",
                "inputSchema": {
                    "type": "object",
                    "properties": {"m": {"type": "string",
                                         "enum": list("abcdefgh")}},
                    "required": ["m"],
                },
            }},
        })
        p = payload_of(r)
        self.assertEqual(p["total_guessing_cost"], 8)
        self.assertTrue(p["unguessable"])

    def test_corpus_stats(self):
        r = rpc("tools/call", {"name": "corpus_stats", "arguments": {}})
        p = payload_of(r)
        self.assertGreater(p["total_entries"], 0)
        self.assertEqual(
            p["verified_entries"] + p["claimed_entries"], p["total_entries"]
        )

    def test_list_tools_of(self):
        r = rpc("tools/call", {"name": "list_tools_of",
                               "arguments": {"server": "rea"}})
        p = payload_of(r)
        self.assertGreater(p["tool_count"], 0)
        for t in p["tools"]:
            self.assertGreater(t["verified_calls"], 0)

    def test_record_call_without_evidence_is_refused(self):
        """The required-arg gate must block a call with no evidence."""
        r = rpc("tools/call", {
            "name": "record_call",
            "arguments": {"server": "s", "tool": "t", "summary": "x"},
        })
        self.assertTrue(r["result"]["isError"])
        self.assertIn("missing required argument", payload_of(r)["error"])
        self.assertIn("evidence", payload_of(r)["error"])

    def test_record_call_with_blank_evidence_is_refused(self):
        """Passing the arg but with an empty value must still be refused."""
        r = rpc("tools/call", {
            "name": "record_call",
            "arguments": {"server": "s", "tool": "t", "summary": "x",
                          "evidence": ""},
        })
        self.assertTrue(r["result"]["isError"])
        self.assertIn("evidence is required", payload_of(r)["error"])

    def test_unknown_method_returns_jsonrpc_error(self):
        r = rpc("no/such/method", {})
        self.assertIn("error", r)
        self.assertEqual(r["error"]["code"], -32601)


if __name__ == "__main__":
    unittest.main(verbosity=2)
