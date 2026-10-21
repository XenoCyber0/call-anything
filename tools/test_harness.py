#!/usr/bin/env python3
"""Tests for the probe/harness layer.

These use a FAKE MCP server (a real subprocess speaking JSON-RPC over
stdio) so the discovery logic is exercised end to end without depending
on any external server being installed.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools"))

import call_core as core            # noqa: E402
import probe as P                   # noqa: E402
from harness import build_entries, probe_server  # noqa: E402
from seed_from_server import MCPClient  # noqa: E402


FAKE_SERVER = textwrap.dedent('''
    #!/usr/bin/env python3
    """A tiny MCP server with a known, testable behaviour profile."""
    import json, sys

    TOOLS = [
        {
            "name": "ping_tool",
            "description": "Always succeeds with no arguments",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "greet_tool",
            "description": "Needs a name",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "enum": ["alice", "bob"]},
                },
                "required": ["name"],
            },
        },
        {
            "name": "needs_target",
            "description": "Requires an open target",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "never_works",
            "description": "Always errors",
            "inputSchema": {"type": "object", "properties": {}},
        },
    ]

    def send(o):
        sys.stdout.write(json.dumps(o) + "\\n")
        sys.stdout.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req = json.loads(line)
        m = req.get("method")
        rid = req.get("id")
        if m == "initialize":
            send({"jsonrpc": "2.0", "id": rid, "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fake", "version": "9.9.9"}}})
        elif m == "tools/list":
            send({"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}})
        elif m == "tools/call":
            name = req["params"]["name"]
            args = req["params"].get("arguments") or {}
            if name == "ping_tool":
                send({"jsonrpc": "2.0", "id": rid, "result": {
                    "content": [{"type": "text",
                                 "text": json.dumps({"result": {"pong": True}})}]}})
            elif name == "greet_tool":
                if "name" not in args:
                    send({"jsonrpc": "2.0", "id": rid, "result": {
                        "isError": True,
                        "content": [{"type": "text",
                                     "text": json.dumps({"error": {
                                         "code": "invalid_arguments",
                                         "message": "missing required argument name"}})}]}})
                else:
                    send({"jsonrpc": "2.0", "id": rid, "result": {
                        "content": [{"type": "text",
                                     "text": json.dumps({"result": {
                                         "greeting": "hello " + str(args["name"])}})}]}})
            elif name == "needs_target":
                send({"jsonrpc": "2.0", "id": rid, "result": {
                    "content": [{"type": "text", "text": json.dumps({
                        "error": {"code": "target_unavailable",
                                  "category": "unavailable",
                                  "message": "No app is open. Ask the user to open one."}})}]}})
            else:
                send({"jsonrpc": "2.0", "id": rid, "result": {
                    "isError": True,
                    "content": [{"type": "text", "text": "something went wrong"}]}})
        elif m and m.startswith("notifications/"):
            continue
        else:
            send({"jsonrpc": "2.0", "id": rid, "result": {}})
''')


class FakeServer:
    """A real subprocess MCP server with known behaviour."""

    def __init__(self):
        fd, self.path = tempfile.mkstemp(suffix=".py")
        with os.fdopen(fd, "w") as fh:
            fh.write(FAKE_SERVER)
        self.client = MCPClient([sys.executable, self.path])

    def close(self):
        self.client.close()
        try:
            os.unlink(self.path)
        except OSError:
            pass


class TestProbePlanning(unittest.TestCase):
    def test_bare_call_is_always_planned_first(self):
        tool = {"name": "t", "inputSchema": {"type": "object", "properties": {}}}
        self.assertEqual(P.plan_probes(tool)[0], {})

    def test_required_arg_gets_its_enum_values(self):
        tool = {
            "name": "t",
            "inputSchema": {
                "type": "object",
                "properties": {"mode": {"type": "string", "enum": ["a", "b"]}},
                "required": ["mode"],
            },
        }
        combos = P.plan_probes(tool)
        self.assertIn({"mode": "a"}, combos)
        self.assertIn({"mode": "b"}, combos)

    def test_defaults_are_tried(self):
        tool = {
            "name": "t",
            "inputSchema": {
                "type": "object",
                "properties": {"x": {"type": "string", "default": "d"}},
                "required": ["x"],
            },
        }
        self.assertIn({"x": "d"}, P.plan_probes(tool))

    def test_probes_are_bounded_and_deduped(self):
        tool = {
            "name": "t",
            "inputSchema": {
                "type": "object",
                "properties": {f"p{i}": {"type": "string",
                                         "enum": ["a", "b", "c"]}
                               for i in range(8)},
                "required": [f"p{i}" for i in range(8)],
            },
        }
        combos = P.plan_probes(tool, max_per_tool=10)
        self.assertLessEqual(len(combos), 10)
        self.assertEqual(len(combos), len({json.dumps(c, sort_keys=True)
                                           for c in combos}))

    def test_candidates_prefer_enum_over_common_values(self):
        prop = {"type": "string", "enum": ["special"]}
        self.assertEqual(P._candidates(prop)[0], "special")

    def test_useless_values_are_dropped(self):
        prop = {"type": "string", "default": "null"}
        self.assertNotIn("null", [str(v) for v in P._candidates(prop)])


class TestInterpret(unittest.TestCase):
    def test_jsonrpc_envelope_is_unwrapped(self):
        resp = {"jsonrpc": "2.0", "id": 1, "result": {
            "content": [{"type": "text", "text": "hello"}]}}
        ok, out, code, msg = P.interpret("t", resp)
        self.assertTrue(ok)
        self.assertEqual(out, "hello")

    def test_error_inside_content_is_a_failure(self):
        resp = {"result": {"content": [{"type": "text", "text": json.dumps(
            {"error": {"code": "bad", "message": "nope"}})}]}}
        ok, out, code, msg = P.interpret("t", resp)
        self.assertFalse(ok)
        self.assertEqual(code, "bad")

    def test_iserror_is_a_failure(self):
        resp = {"result": {"isError": True,
                           "content": [{"type": "text", "text": "boom"}]}}
        ok, _, code, _ = P.interpret("t", resp)
        self.assertFalse(ok)

    def test_empty_output_is_a_failure(self):
        resp = {"result": {"content": []}}
        ok, _, code, _ = P.interpret("t", resp)
        self.assertFalse(ok)


class TestClassify(unittest.TestCase):
    def test_precondition_detected(self):
        p = P.Probe(tool="t", arguments={}, ok=False,
                    error_code="target_unavailable",
                    error_message="No app is open.")
        self.assertEqual(P.classify_failure(p), "precondition")

    def test_missing_arg_detected(self):
        p = P.Probe(tool="t", arguments={}, ok=False,
                    error_code="invalid_arguments",
                    error_message="missing required argument name")
        self.assertEqual(P.classify_failure(p), "needs_args")

    def test_success(self):
        p = P.Probe(tool="t", arguments={}, ok=True)
        self.assertEqual(P.classify_failure(p), "ok")

    def test_other(self):
        p = P.Probe(tool="t", arguments={}, ok=False,
                    error_code="weird", error_message="something went wrong")
        self.assertEqual(P.classify_failure(p), "other")


class TestHarnessEndToEnd(unittest.TestCase):
    """Drive the real harness against the fake server."""

    @classmethod
    def setUpClass(cls):
        cls.srv = FakeServer()
        cls.client = cls.srv.client
        cls.client.initialize()
        cls.tools = cls.client.list_tools()

    @classmethod
    def tearDownClass(cls):
        cls.srv.close()

    def test_finds_the_bare_callable_tool(self):
        findings = probe_server(self.client, self.tools, 12, verbose=False)
        self.assertTrue(findings["ping_tool"].working)
        self.assertEqual(findings["ping_tool"].working[0], {})

    def test_finds_the_enum_argument(self):
        findings = probe_server(self.client, self.tools, 12, verbose=False)
        self.assertTrue(findings["greet_tool"].working)
        self.assertIn(findings["greet_tool"].working[0].get("name"),
                      ("alice", "bob"))

    def test_records_the_precondition(self):
        findings = probe_server(self.client, self.tools, 12, verbose=False)
        self.assertFalse(findings["needs_target"].working)
        self.assertTrue(findings["needs_target"].preconditions)
        self.assertIn("No app is open",
                      findings["needs_target"].preconditions[0])

    def test_raw_error_is_preserved(self):
        findings = probe_server(self.client, self.tools, 12, verbose=False)
        raw = findings["needs_target"].raw_errors
        self.assertTrue(raw)
        self.assertIn("target_unavailable", raw[0])

    def test_entries_are_built_and_all_carry_evidence(self):
        findings = probe_server(self.client, self.tools, 12, verbose=False)
        entries = build_entries("fake", "9.9.9", self.tools, findings,
                                "2026-10-21")
        self.assertTrue(entries)
        for e in entries:
            self.assertTrue(e.evidence, f"{e.id} has no evidence")
            self.assertEqual(e.server, "fake")
            self.assertEqual(e.verified_by, "recorded")

    def test_generated_corpus_passes_the_validator(self):
        findings = probe_server(self.client, self.tools, 12, verbose=False)
        entries = build_entries("fake", "9.9.9", self.tools, findings,
                                "2026-10-21")
        import yaml
        fd, path = tempfile.mkstemp(suffix=".yaml")
        with os.fdopen(fd, "w") as fh:
            yaml.safe_dump({"version": 1, "calls": [e.to_dict() for e in entries]},
                           fh)
        try:
            ok, errors = core.validate_corpus(path)
            self.assertTrue(ok, f"generated corpus invalid: {errors}")
        finally:
            os.unlink(path)

    def test_report_shape(self):
        findings = probe_server(self.client, self.tools, 12, verbose=False)
        entries = build_entries("fake", "9.9.9", self.tools, findings,
                                "2026-10-21")
        self.assertTrue(any(e.tool == "ping_tool" for e in entries))
        self.assertTrue(any("precondition" in e.tags for e in entries))


if __name__ == "__main__":
    unittest.main(verbosity=2)
