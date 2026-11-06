#!/usr/bin/env python3
"""Seed the corpus by ACTUALLY EXECUTING calls against a real MCP server.

Nothing enters the corpus without a real recorded result. This is the
whole point: an entry exists only because a call was made and worked.

    python3 tools/seed_from_server.py --server rea \
        --cmd "node /path/to/rea/scripts/rea.mjs mcp" \
        --call list_architectures \
        --call list_documents
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools"))

import call_core as core  # noqa: E402


def resolve_command(cmd: list[str]) -> list[str]:
    """Make a command runnable on Windows.

    On Windows, `npx`, `npm` and friends are `.cmd` shims. `subprocess`
    with a list argument and no `shell=True` does not look for PATHEXT, so
    spawning them raises FileNotFoundError even though the command is
    installed and on PATH.

    The fix is to resolve the executable to its real path, appending the
    PATHEXT extension when needed. On POSIX this is a no-op.
    """
    if not cmd:
        return cmd
    exe = cmd[0]
    if os.name != "nt":
        return cmd

    # Already an explicit path to a file that exists -- leave it alone.
    if os.path.isfile(exe):
        return cmd

    # Resolve against PATH, honouring PATHEXT.
    path_env = os.environ.get("PATH", "")
    exts = [""]
    pathext = os.environ.get("PATHEXT", "")
    if pathext:
        exts += [e.lower() for e in pathext.split(os.pathsep) if e]
    seen = set()
    for directory in path_env.split(os.pathsep):
        if not directory:
            continue
        for ext in exts:
            if ext in seen:
                continue
            seen.add(ext)
            candidate = os.path.join(directory, exe + ext)
            if os.path.isfile(candidate):
                return [candidate] + list(cmd[1:])

    return cmd


class MCPClient:
    """Minimal MCP stdio client."""

    def __init__(self, cmd: list[str]):
        self.proc = subprocess.Popen(
            resolve_command(cmd),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        self._id = 0

    def _send(self, obj: dict) -> None:
        assert self.proc.stdin
        self.proc.stdin.write(json.dumps(obj) + "\n")
        self.proc.stdin.flush()

    def _read(self):
        assert self.proc.stdout
        while True:
            line = self.proc.stdout.readline()
            if not line:
                return None
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue

    def request(self, method: str, params: dict | None = None):
        self._id += 1
        self._send({
            "jsonrpc": "2.0", "id": self._id, "method": method,
            "params": params or {},
        })
        while True:
            msg = self._read()
            if msg is None:
                raise RuntimeError(f"server closed during {method}")
            if msg.get("id") == self._id:
                return msg

    def notify(self, method: str, params: dict | None = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def initialize(self):
        r = self.request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "call-anything-seeder", "version": "0.1.0"},
        })
        self.notify("notifications/initialized")
        return r

    def list_tools(self):
        r = self.request("tools/list", {})
        return r.get("result", {}).get("tools", [])

    def call(self, name: str, arguments: dict):
        r = self.request("tools/call", {"name": name, "arguments": arguments})
        return r

    def close(self):
        try:
            self.proc.terminate()
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()


def summarize_result(resp: dict) -> tuple[bool, str]:
    """Turn a tools/call response into (ok, evidence_text)."""
    res = resp.get("result")
    if res is None:
        err = resp.get("error") or {}
        return False, f"ERROR {err.get('code')}: {err.get('message')}"
    if res.get("isError"):
        texts = [
            c.get("text", "") for c in res.get("content", [])
            if c.get("type") == "text"
        ]
        return False, " ".join(texts)[:2000]
    texts = [
        c.get("text", "") for c in res.get("content", [])
        if c.get("type") == "text"
    ]
    return True, "\n".join(texts)[:2000]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", required=True)
    ap.add_argument("--cmd", required=True,
                    help="command to launch the MCP server (shell-split)")
    ap.add_argument("--call", action="append", default=[],
                    help="tool name (repeatable)")
    ap.add_argument("--args", action="append", default=[],
                    help="JSON object for the matching --call (repeatable)")
    ap.add_argument("--summary", action="append", default=[])
    ap.add_argument("--tags", default="")
    ap.add_argument("--version", default="")
    ap.add_argument("--corpus", default=os.path.join(REPO, "data", "calls.yaml"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cmd = args.cmd.split()
    client = MCPClient(cmd)
    try:
        init = client.initialize()
        srv = init.get("result", {}).get("serverInfo", {})
        version = args.version or srv.get("version", "")
        tools = {t["name"]: t for t in client.list_tools()}
        print(f"connected to {srv.get('name')} {srv.get('version')}")
        print(f"{len(tools)} tools advertised")

        arg_map = {}
        for i, a in enumerate(args.args):
            arg_map.setdefault(i, json.loads(a))

        results = []
        for i, tool_name in enumerate(args.call):
            if tool_name not in tools:
                print(f"  SKIP {tool_name}: not advertised by this server")
                continue
            call_args = arg_map.get(i, {})
            resp = client.call(tool_name, call_args)
            ok, evidence = summarize_result(resp)
            status = "OK " if ok else "FAIL"
            print(f"  {status} {tool_name} -> {evidence[:90]!r}")
            if not ok:
                continue
            results.append({
                "tool": tool_name,
                "arguments": call_args,
                "evidence": evidence,
                "summary": (args.summary[i] if i < len(args.summary)
                            else f"verified call to {tool_name}"),
            })
    finally:
        client.close()

    if not results:
        print("no successful calls; corpus unchanged")
        return 1

    entries = []
    try:
        entries = core.load_corpus(args.corpus)
    except core.CorpusError:
        entries = []

    import datetime
    today = datetime.date.today().isoformat()
    added = 0
    for r in results:
        if core.find_by_call(entries, args.server, r["tool"], r["arguments"]):
            print(f"  already present: {r['tool']}")
            continue
        e = core.CallEntry(
            id=core.next_id(entries, args.server, r["tool"]),
            server=args.server,
            tool=r["tool"],
            arguments=r["arguments"],
            summary=r["summary"],
            evidence=r["evidence"],
            verified_by="recorded",
            verified_at=today,
            server_version=version,
            tags=[t for t in args.tags.split(",") if t],
        )
        entries.append(e)
        added += 1
        print(f"  + {e.id}")

    if args.dry_run:
        print(f"dry run: {added} would be added")
        return 0

    import yaml
    doc = {"version": 1, "calls": []}
    for e in entries:
        d = e.to_dict()
        d.pop("call_id", None)
        d.pop("is_verified", None)
        doc["calls"].append(d)
    with open(args.corpus, "w", encoding="utf-8") as fh:
        yaml.safe_dump(doc, fh, sort_keys=False, allow_unicode=True,
                       default_flow_style=False, width=100)
    print(f"wrote {len(entries)} entries to {args.corpus}")

    # The corpus and CALLS.md are two views of the same data. Writing the
    # corpus without regenerating the index leaves them inconsistent, and
    # `tools/build_calls.py --check` (the CI gate) then fails with "index
    # is stale" -- a self-inflicted red build caused by using the seeder
    # exactly as CONTRIBUTING describes. The seeder is the PREFERRED way
    # to add a call, so it must leave the index in sync.
    try:
        import build_calls
        index = build_calls.regenerate_index(args.corpus)
        print(f"regenerated {index}")
    except Exception as exc:
        print(
            f"WARNING: could not regenerate the index ({exc}); "
            f"run `python3 tools/build_calls.py` before committing",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
