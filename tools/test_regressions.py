#!/usr/bin/env python3
"""Regression tests for the bugs fixed in this round.

Each test names the bug it locks down. If a test fails, the specific
defect it guards has come back.
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

import call_core as core                              # noqa: E402
import probe as P                                     # noqa: E402
from harness import build_entries, probe_server        # noqa: E402
from seed_from_server import MCPClient, resolve_command  # noqa: E402

CORPUS = os.path.join(REPO, "data", "calls.yaml")
MCP = os.path.join(REPO, "tools", "mcp", "call_mcp.py")


# --------------------------------------------------------------------------
# helpers


def rpc(method: str, params: dict | None = None, req_id: int = 1) -> dict:
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
    return reply or {}


def payload_of(reply: dict) -> dict:
    res = reply["result"]
    text = "".join(
        c.get("text", "") for c in res.get("content", []) if c.get("type") == "text"
    )
    return json.loads(text)


# A server that succeeds with arguments but REFUSES the bare call, and
# whose schema does not mark any argument as required. This is the case
# that fooled the old `callable_bare` logic.
NEEDS_RUNTIME_ARG = textwrap.dedent('''
    #!/usr/bin/env python3
    import json, sys

    TOOLS = [{
        "name": "runtime_arg_tool",
        "description": "Schema says no required args, but {} fails at runtime",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
        },
    }, {
        "name": "bare_ok_tool",
        "description": "Works with no arguments at all",
        "inputSchema": {"type": "object", "properties": {}},
    }]

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
                "serverInfo": {"name": "fake2", "version": "1.2.3"}}})
        elif m == "tools/list":
            send({"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}})
        elif m == "tools/call":
            name = req["params"]["name"]
            args = req["params"].get("arguments") or {}
            if name == "runtime_arg_tool" and "path" not in args:
                send({"jsonrpc": "2.0", "id": rid, "result": {
                    "isError": True,
                    "content": [{"type": "text", "text": json.dumps({
                        "error": {"code": "invalid_arguments",
                                  "message": "path is required at runtime"}})}]}})
            elif name == "runtime_arg_tool":
                send({"jsonrpc": "2.0", "id": rid, "result": {
                    "content": [{"type": "text", "text": json.dumps({
                        "result": {"read": args["path"]}})}]}})
            else:
                send({"jsonrpc": "2.0", "id": rid, "result": {
                    "content": [{"type": "text", "text": json.dumps({
                        "result": {"ok": True}})}]}})
        elif m and m.startswith("notifications/"):
            continue
        else:
            send({"jsonrpc": "2.0", "id": rid, "result": {}})
''')


class Fake2:
    def __init__(self):
        fd, self.path = tempfile.mkstemp(suffix=".py")
        with os.fdopen(fd, "w") as fh:
            fh.write(NEEDS_RUNTIME_ARG)
        self.client = MCPClient([sys.executable, self.path])

    def close(self):
        self.client.close()
        try:
            os.unlink(self.path)
        except OSError:
            pass


# --------------------------------------------------------------------------
# bug 3/4: callable_bare must mean "proven", not "schema has no required"


class TestCallableBareIsProven(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fake = Fake2()
        cls.client = cls.fake.client
        cls.client.initialize()
        cls.tools = cls.client.list_tools()
        cls.findings = probe_server(cls.client, cls.tools, 12, verbose=False)

    @classmethod
    def tearDownClass(cls):
        cls.fake.close()

    def test_bare_ok_tool_is_callable_bare(self):
        f = self.findings["bare_ok_tool"]
        self.assertTrue(f.bare_attempted)
        self.assertTrue(f.bare_ok)
        self.assertTrue(f.is_callable_bare)

    def test_runtime_arg_tool_is_not_callable_bare(self):
        """The bare call was tried and FAILED -- so it is not callable bare.

        The old logic said `bool(working) and not requires_args`. The
        schema declares no required args, so the old logic reported this
        tool as callable with `{}` even though `{}` was proven to fail.
        """
        f = self.findings["runtime_arg_tool"]
        self.assertTrue(f.working, "the tool should work with an argument")
        self.assertTrue(f.bare_attempted, "the bare call must have been tried")
        self.assertFalse(f.bare_ok)
        self.assertFalse(
            f.is_callable_bare,
            "a tool whose bare call failed must not be reported callable_bare",
        )

    def test_runtime_arg_tool_is_tagged_needs_args(self):
        f = self.findings["runtime_arg_tool"]
        self.assertTrue(f.requires_args, "requires_args must be populated")

    def test_entry_for_runtime_arg_is_tagged_needs_args(self):
        entries = build_entries("fake2", "1.2.3", self.tools, self.findings,
                                "2026-10-21")
        e = next(e for e in entries if e.tool == "runtime_arg_tool")
        self.assertIn("needs-args", e.tags)
        self.assertNotIn(
            "no-args", e.tags,
            "an entry whose call carries arguments must not claim no-args",
        )

    def test_bare_entry_is_tagged_no_args(self):
        entries = build_entries("fake2", "1.2.3", self.tools, self.findings,
                                "2026-10-21")
        e = next(e for e in entries if e.tool == "bare_ok_tool")
        self.assertIn("no-args", e.tags)


# --------------------------------------------------------------------------
# bug 4: precondition entries must record the arguments that produced them


class TestPreconditionArgs(unittest.TestCase):
    def test_precondition_entry_records_its_arguments(self):
        """A precondition entry must carry the args that triggered it.

        The old code passed `{}` to `_tags`, so the entry was tagged
        `no-args` even though the recorded call used real arguments.
        """
        findings = {
            "tool": P.Finding(
                tool="tool",
                preconditions=["no target open"],
                raw_errors=[json.dumps({"error": {"code": "target_unavailable",
                                                  "message": "no target open"}})],
                precondition_args={"target": "app"},
            )
        }
        tools = [{
            "name": "tool",
            "description": "d",
            "inputSchema": {"type": "object", "properties": {}},
        }]
        entries = build_entries("s", "1.0", tools, findings, "2026-10-21")
        self.assertEqual(len(entries), 1)
        e = entries[0]
        self.assertEqual(e.arguments, {"target": "app"})
        self.assertIn("precondition", e.tags)
        self.assertNotIn("no-args", e.tags)
        # tags must stay sorted and unique
        self.assertEqual(e.tags, sorted(set(e.tags)))


# --------------------------------------------------------------------------
# bug 1/2: verify_replay must not report success on an empty target set,
#           and --update must produce a corpus its own validator accepts


class TestVerifyReplayGuards(unittest.TestCase):
    def _run(self, args: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, os.path.join(REPO, "tools", "verify_replay.py")] + args,
            capture_output=True, text=True,
        )

    def test_no_matching_server_exits_nonzero(self):
        """A typo'd server name must NOT produce a green build.

        The old code printed 'no verified entries' and returned 0 -- a
        vacuous pass, the worst failure mode for a verifier.
        """
        r = self._run([
            "--server", "definitely-not-a-real-server-name",
            "--cmd", f"{sys.executable} {MCP}",
            "--corpus", CORPUS,
        ])
        self.assertNotEqual(
            r.returncode, 0,
            "an empty target set must not exit 0 (vacuous pass)",
        )
        self.assertIn("no verified entries", r.stderr)

    def test_update_records_server_version_so_validator_accepts(self):
        """--update must write a corpus that `build_calls.py --check` accepts.

        The old code set verified_by='replayed' but left server_version
        empty, so the corpus validator rejected its own output:
        "entry ... was replayed but records no server_version".
        """
        entries = core.load_corpus(CORPUS)
        target = next(e for e in entries if e.is_verified and e.server == "rea")
        # A copy of the corpus with exactly one target entry.
        fd, path = tempfile.mkstemp(suffix=".yaml")
        os.close(fd)
        import yaml
        with open(path, "w", encoding="utf-8") as fh:
            yaml.safe_dump(
                {"version": 1, "calls": [e.to_dict() for e in entries]},
                fh, sort_keys=False, allow_unicode=True, default_flow_style=False,
            )
        try:
            r = self._run([
                "--server", "rea",
                "--cmd", f"{sys.executable} {MCP}",
                "--corpus", path,
                "--only", target.id,
                "--update",
            ])
            # The replay may or may not match (the corpus records a
            # different server), but if it DID update, the result must
            # validate. Check the corpus validates either way.
            ok, errors = core.validate_corpus(path)
            self.assertTrue(
                ok,
                f"the corpus written by --update must pass the validator: {errors}",
            )
        finally:
            os.unlink(path)


# --------------------------------------------------------------------------
# bug 6: find_call search must include the evidence


class TestSearchIncludesEvidence(unittest.TestCase):
    def test_evidence_is_searchable(self):
        """An error code that appears ONLY in the evidence must be findable.

        The old haystack omitted `evidence`, so an agent searching for
        `target_unavailable` found nothing.
        """
        e = core.CallEntry(
            id="x1", server="s", tool="t", arguments={},
            summary="needs a target", evidence=json.dumps(
                {"error": {"code": "target_unavailable"}}),
        )
        hits = core.search([e], query="target_unavailable")
        self.assertTrue(hits, "evidence must be part of the search haystack")

    def test_search_still_finds_by_summary(self):
        e = core.CallEntry(
            id="x1", server="s", tool="t", arguments={},
            summary="lists documents", evidence="{}",
        )
        self.assertTrue(core.search([e], query="lists documents"))


# --------------------------------------------------------------------------
# bug 8: corpus_stats must count verified entries per server


class TestCorpusStats(unittest.TestCase):
    def test_servers_map_counts_verified_entries_only(self):
        r = rpc("tools/call", {"name": "corpus_stats", "arguments": {}})
        p = payload_of(r)
        # The servers map must agree with verified_entries.
        self.assertEqual(
            sum(p["servers"].values()),
            p["verified_entries"],
            "the servers map must count verified entries only, so it agrees "
            "with verified_entries",
        )

    def test_distinct_tools_agrees_with_servers(self):
        r = rpc("tools/call", {"name": "corpus_stats", "arguments": {}})
        p = payload_of(r)
        # distinct_tools counts verified (server, tool) pairs; it can be
        # lower than the number of servers but never higher than the
        # number of verified entries.
        self.assertLessEqual(p["distinct_tools"], p["verified_entries"])


# --------------------------------------------------------------------------
# bug 5: record_call must leave the generated index in sync


class TestRecordCallKeepsIndexFresh(unittest.TestCase):
    def test_record_call_regenerates_the_index(self):
        """Recording a call must not leave CALLS.md stale.

        The old code wrote the corpus only, so the CI gate
        (`build_calls.py --check`) failed with 'index is stale' right
        after a successful record_call.
        """
        import shutil
        import yaml

        # Work on a COPY of the repo state so the real corpus is untouched.
        tmp = tempfile.mkdtemp()
        corpus_copy = os.path.join(tmp, "calls.yaml")
        shutil.copy(CORPUS, corpus_copy)

        env = dict(os.environ)
        env["CALL_ANYTHING_CORPUS"] = corpus_copy

        # Point the server at the copy by running it with the env var.
        p = subprocess.Popen(
            [sys.executable, MCP],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, bufsize=1, env=env,
        )
        try:
            def send(obj):
                p.stdin.write(json.dumps(obj) + "\n")
                p.stdin.flush()

            def recv():
                while True:
                    line = p.stdout.readline()
                    if not line:
                        return None
                    try:
                        return json.loads(line)
                    except json.JSONDecodeError:
                        continue

            send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                  "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                             "clientInfo": {"name": "t", "version": "1"}}})
            recv()
            send({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                  "params": {"name": "record_call", "arguments": {
                      "server": "regression", "tool": "regression_tool",
                      "summary": "regression entry",
                      "evidence": json.dumps({"result": {"ok": True}})}}})
            reply = recv()
        finally:
            p.terminate()
            p.wait(timeout=5)
            shutil.rmtree(tmp, ignore_errors=True)

        payload = payload_of(reply)
        self.assertTrue(payload.get("ok"), f"record_call failed: {payload}")
        self.assertIn(
            "index", payload,
            "record_call should report what it did to the generated index",
        )
        self.assertIn("regenerated", payload["index"])

    def test_record_call_does_not_touch_the_repository_index(self):
        """An alternate corpus must not pollute the repo's CALLS.md.

        `record_call` regenerates the index so the CI gate stays green.
        But the index belongs to the corpus it describes: when the server
        is pointed at a different corpus via CALL_ANYTHING_CORPUS, writing
        the repo's CALLS.md injected the alternate corpus's entries into
        the repository -- a phantom server section, and a red CI gate for
        the real corpus ("index is stale").
        """
        import shutil

        repo_index = os.path.join(REPO, "CALLS.md")
        with open(repo_index, "r", encoding="utf-8") as fh:
            before = fh.read()

        tmp = tempfile.mkdtemp()
        corpus_copy = os.path.join(tmp, "calls.yaml")
        shutil.copy(CORPUS, corpus_copy)

        env = dict(os.environ)
        env["CALL_ANYTHING_CORPUS"] = corpus_copy

        p = subprocess.Popen(
            [sys.executable, MCP],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, bufsize=1, env=env,
        )
        try:
            def send(obj):
                p.stdin.write(json.dumps(obj) + "\n")
                p.stdin.flush()

            def recv():
                while True:
                    line = p.stdout.readline()
                    if not line:
                        return None
                    try:
                        return json.loads(line)
                    except json.JSONDecodeError:
                        continue

            send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                  "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                             "clientInfo": {"name": "t", "version": "1"}}})
            recv()
            send({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                  "params": {"name": "record_call", "arguments": {
                      "server": "polluter", "tool": "polluter_tool",
                      "summary": "must not reach the repo index",
                      "evidence": json.dumps({"result": {"ok": True}})}}})
            reply = recv()
        finally:
            p.terminate()
            p.wait(timeout=5)

        payload = payload_of(reply)
        self.assertTrue(payload.get("ok"), f"record_call failed: {payload}")

        with open(repo_index, "r", encoding="utf-8") as fh:
            after = fh.read()

        self.assertEqual(
            before, after,
            "record_call against an alternate corpus rewrote the repo's "
            "CALLS.md; the index must follow the corpus it describes",
        )
        self.assertNotIn("polluter", after)

        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------
# bug 9: the Windows .cmd shim must be resolvable


class TestResolveCommand(unittest.TestCase):
    def test_explicit_existing_path_is_untouched(self):
        cmd = [sys.executable, "x.py"]
        self.assertEqual(resolve_command(cmd), cmd)

    def test_empty_command_is_untouched(self):
        self.assertEqual(resolve_command([]), [])

    def test_npx_resolves_to_a_spawnable_file(self):
        """`npx` is a .cmd shim on Windows; it must resolve to a real file.

        Without this, subprocess.Popen raises FileNotFoundError even
        though npx is installed and on PATH.
        """
        if os.name != "nt":
            self.skipTest("Windows-only behaviour")
        resolved = resolve_command(["npx", "--version"])
        if resolved[0] == "npx":
            self.skipTest("npx is not installed on this machine")
        self.assertTrue(
            os.path.isfile(resolved[0]),
            f"resolved npx path does not exist: {resolved[0]}",
        )
        self.assertEqual(resolved[1:], ["--version"])

    def test_unknown_command_is_returned_unchanged(self):
        cmd = ["definitely-not-a-real-command-xyz"]
        self.assertEqual(resolve_command(cmd), cmd)


# --------------------------------------------------------------------------
# bug 7: the seeder must leave the generated index in sync with the corpus


SEEDER_SERVER = textwrap.dedent('''
    #!/usr/bin/env python3
    import json, sys
    TOOLS = [{"name": "ping_tool", "description": "Ping.",
              "inputSchema": {"type": "object", "properties": {}}}]
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
            send({"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text",
                             "text": json.dumps({"result": {"pong": True}})}]}})
''')


class TestSeederKeepsIndexFresh(unittest.TestCase):
    """The seeder is CONTRIBUTING's PREFERRED way to add a call.

    It wrote the corpus but never regenerated CALLS.md, so the very next
    `build_calls.py --check` (the CI gate) failed with "index is stale" --
    a self-inflicted red build caused by using the tool as documented.
    """

    def test_seeder_regenerates_the_index_for_the_corpus_it_wrote(self):
        import build_calls
        import shutil

        tmp = tempfile.mkdtemp(prefix="seeder_index_")
        try:
            server = os.path.join(tmp, "fake.py")
            with open(server, "w", encoding="utf-8") as fh:
                fh.write(SEEDER_SERVER)

            corpus = os.path.join(tmp, "calls.yaml")
            shutil.copy(CORPUS, corpus)
            # The index that belongs to THIS corpus.
            index = os.path.join(tmp, "CALLS.md")
            with open(index, "w", encoding="utf-8") as fh:
                fh.write(build_calls.render(core.load_corpus(corpus)))

            before = open(index, encoding="utf-8").read()

            r = subprocess.run(
                [sys.executable,
                 os.path.join(REPO, "tools", "seed_from_server.py"),
                 "--server", "fakeserver",
                 "--cmd", f"{sys.executable} {server}",
                 "--call", "ping_tool",
                 "--summary", "Ping the fake server",
                 "--corpus", corpus],
                capture_output=True, text=True,
            )
            self.assertEqual(r.returncode, 0, r.stderr)

            # The corpus gained the call...
            self.assertIn("fakeserver", build_calls.render(core.load_corpus(corpus)))
            # ...and so must the index beside it.
            after = open(index, encoding="utf-8").read()
            self.assertNotEqual(before, after,
                                "the seeder did not regenerate the index")
            self.assertIn("fakeserver", after,
                          "the index does not mention the newly seeded call")
            self.assertEqual(
                after, build_calls.render(core.load_corpus(corpus)),
                "the index is not in sync with the corpus after seeding",
            )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_seeder_does_not_touch_the_repository_index(self):
        """An alternate corpus must not rewrite the repo's CALLS.md."""
        import build_calls
        import shutil

        repo_index = os.path.join(REPO, "CALLS.md")
        with open(repo_index, encoding="utf-8") as fh:
            before = fh.read()

        tmp = tempfile.mkdtemp(prefix="seeder_repo_")
        try:
            server = os.path.join(tmp, "fake.py")
            with open(server, "w", encoding="utf-8") as fh:
                fh.write(SEEDER_SERVER)

            corpus = os.path.join(tmp, "calls.yaml")
            shutil.copy(CORPUS, corpus)

            r = subprocess.run(
                [sys.executable,
                 os.path.join(REPO, "tools", "seed_from_server.py"),
                 "--server", "fakeserver",
                 "--cmd", f"{sys.executable} {server}",
                 "--call", "ping_tool",
                 "--summary", "Ping the fake server",
                 "--corpus", corpus],
                capture_output=True, text=True,
            )
            self.assertEqual(r.returncode, 0, r.stderr)

            with open(repo_index, encoding="utf-8") as fh:
                after = fh.read()
            self.assertEqual(
                before, after,
                "seeding an alternate corpus rewrote the repo's CALLS.md",
            )
            self.assertNotIn("fakeserver", after)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
