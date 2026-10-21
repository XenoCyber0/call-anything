#!/usr/bin/env python3
"""harness -- exercise a live MCP server and emit a verified call corpus.

This is the distribution mechanism. A server author runs this in CI:

    python3 tools/harness.py --server my-server \
        --cmd "node ./dist/index.js" \
        --out calls.yaml

It will:
  * connect to the server
  * read its tools/list
  * PROBE each tool with candidate arguments taken from the server's own
    schema (enums, defaults, examples) plus a tiny common-value list
  * keep only the calls that actually SUCCEEDED, with the real output
  * record precondition failures, which teach the order of operations
  * write a corpus in exactly the format Call-Anything reads
  * report a machine-readable conformance summary

No model. No network. No API key. Just: run the server, see what works.

Exit codes:
    0  at least one working call was found
    1  no working calls, or the server could not be reached
    2  the server exposed no tools
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import datetime

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools"))

import call_core as core            # noqa: E402
import probe as P                   # noqa: E402
from seed_from_server import MCPClient  # noqa: E402


def probe_server(client: MCPClient, tools: list[dict], max_per_tool: int,
                 verbose: bool = True) -> dict[str, P.Finding]:
    """Probe every tool. Returns a Finding per tool."""
    findings: dict[str, P.Finding] = {}
    for i, tool in enumerate(tools, 1):
        name = tool.get("name", "?")
        if verbose:
            print(f"  [{i}/{len(tools)}] {name}", end="", flush=True)
        finding = P.Finding(tool=name)
        for args in P.plan_probes(tool, max_per_tool=max_per_tool):
            resp = client.call(name, args)
            p = P.summarise_probe(name, args, resp)
            kind = P.classify_failure(p)
            if kind == "ok":
                finding.working.append(args)
                finding.working_evidence.append(p.output)
                if verbose:
                    print(f" \033[0;32m+ {json.dumps(args)[:48]}\033[0m", end="")
                # One working call per tool is enough; stop probing it.
                break
            elif kind == "precondition":
                msg = (p.error_message or p.error_code or "").strip()
                if msg and msg not in finding.preconditions:
                    finding.preconditions.append(msg)
                if p.raw_output and p.raw_output not in finding.raw_errors:
                    finding.raw_errors.append(p.raw_output)
                # Remember the arguments that produced the precondition, so
                # the recorded entry can be replayed exactly.
                if not finding.precondition_args:
                    finding.precondition_args = dict(args)
                if verbose:
                    print(f" \033[0;33m~ precondition\033[0m", end="")
                break
            elif kind == "needs_args":
                if verbose:
                    print(f" \033[0;90m. needs args\033[0m", end="")
                continue
            else:
                if p.error_message and p.error_message not in finding.errors:
                    finding.errors.append(p.error_message)
                if verbose:
                    print(f" \033[0;31mx {json.dumps(args)[:40]}\033[0m", end="")
        if not finding.working and not finding.preconditions:
            req = (tool.get("inputSchema") or {}).get("required") or []
            finding.requires_args = list(req)
        findings[name] = finding
        if verbose:
            print()
    return findings


def build_entries(server: str, version: str, tools: list[dict],
                  findings: dict[str, P.Finding],
                  today: str) -> list[core.CallEntry]:
    """Turn findings into corpus entries."""
    entries: list[core.CallEntry] = []
    by_name = {t.get("name"): t for t in tools}

    for name, f in findings.items():
        tool = by_name.get(name, {})
        desc = P.describe(tool)

        if f.working:
            for args, evidence in zip(f.working, f.working_evidence):
                e = core.CallEntry(
                    id=core.next_id(entries, server, name),
                    server=server,
                    tool=name,
                    arguments=args,
                    summary=desc,
                    evidence=evidence[:2000],
                    verified_by="recorded",
                    verified_at=today,
                    server_version=version,
                    tags=_tags(tool, f, args),
                )
                entries.append(e)
        elif f.preconditions:
            # A precondition is verified knowledge: this call fails, and
            # here is exactly how. The RAW error is preserved so the
            # replay verifier can compare it shape-for-shape.
            e = core.CallEntry(
                id=core.next_id(entries, server, name),
                server=server,
                tool=name,
                arguments=f.precondition_args or {},
                summary=f"{desc} -- requires a precondition: "
                        f"{f.preconditions[0][:120]}",
                evidence=f.raw_errors[0] if f.raw_errors else json.dumps({
                    "error": {
                        "code": "precondition",
                        "message": f.preconditions[0],
                    }
                }),
                verified_by="recorded",
                verified_at=today,
                server_version=version,
                tags=_tags(tool, f, {}) + ["precondition"],
            )
            entries.append(e)
    return entries


def _tags(tool: dict, f: P.Finding, args: dict) -> list[str]:
    tags = ["verified"]
    if not args:
        tags.append("no-args")
    if f.requires_args:
        tags.append("needs-args")
    schema = tool.get("inputSchema") or {}
    props = schema.get("properties") or {}
    for k, v in (args or {}).items():
        p = props.get(k) or {}
        if isinstance(p.get("enum"), list):
            tags.append(f"enum-{k}")
    return sorted(set(tags))


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Exercise an MCP server and emit a verified call corpus.",
    )
    ap.add_argument("--server", required=True, help="server name to record under")
    ap.add_argument("--cmd", required=True, help="command that starts the server")
    ap.add_argument("--out", default="", help="write the corpus here")
    ap.add_argument("--merge", default="",
                    help="existing corpus to merge into (defaults to --out)")
    ap.add_argument("--max-probes", dest="max_per_tool", type=int, default=12,
                    help="max candidate calls per tool")
    ap.add_argument("--only", default="", help="only probe tools matching this")
    ap.add_argument("--report", default="", help="write a JSON report here")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    verbose = not args.quiet
    client = MCPClient(args.cmd.split())
    try:
        init = client.initialize()
        info = init.get("result", {}).get("serverInfo", {})
        version = str(info.get("version", ""))
        tools = client.list_tools()
    except Exception as exc:
        print(f"could not reach the server: {exc}", file=sys.stderr)
        return 1

    if verbose:
        print(f"server: {info.get('name')} {version}")
        print(f"tools : {len(tools)}")
        print()

    if not tools:
        print("server exposed no tools", file=sys.stderr)
        return 2

    if args.only:
        tools = [t for t in tools if args.only in (t.get("name") or "")]

    today = datetime.date.today().isoformat()
    findings = probe_server(client, tools, args.max_per_tool, verbose=verbose)

    entries = build_entries(args.server, version, tools, findings, today)

    # Merge into an existing corpus if asked.
    target = args.out
    if not target:
        print("\n-- dry run (no --out given) --")
        for e in entries:
            print(f"  {e.id:44s} {json.dumps(e.arguments)[:44]}")
        working = sum(1 for f in findings.values() if f.working)
        print(f"\n{working} tools proven callable, {len(entries)} entries")
        return 0 if working else 1

    existing: list[core.CallEntry] = []
    merge_path = args.merge or target
    if os.path.exists(merge_path):
        try:
            existing = core.load_corpus(merge_path)
        except core.CorpusError as exc:
            print(f"could not read {merge_path}: {exc}", file=sys.stderr)
            return 1

    kept = 0
    for e in entries:
        if core.find_by_call(existing, e.server, e.tool, e.arguments):
            continue
        existing.append(e)
        kept += 1

    import yaml
    doc = {"version": 1, "calls": []}
    for e in existing:
        d = e.to_dict()
        d.pop("call_id", None)
        d.pop("is_verified", None)
        doc["calls"].append(d)
    os.makedirs(os.path.dirname(os.path.abspath(target)) or ".", exist_ok=True)
    with open(target, "w", encoding="utf-8") as fh:
        yaml.safe_dump(doc, fh, sort_keys=False, allow_unicode=True,
                       default_flow_style=False, width=100)

    working = sum(1 for f in findings.values() if f.working)
    precond = sum(1 for f in findings.values() if f.preconditions and not f.working)

    if verbose:
        print(f"\ntools probed      : {len(findings)}")
        print(f"proven callable   : {working}")
        print(f"precondition only : {precond}")
        print(f"new entries added : {kept}")
        print(f"corpus            : {target} ({len(existing)} total)")

    if args.report:
        report = {
            "server": args.server,
            "server_name": info.get("name"),
            "server_version": version,
            "tools_advertised": len(tools),
            "tools_probed": len(findings),
            "tools_callable": working,
            "tools_precondition_only": precond,
            "tools_unproven": len(findings) - working - precond,
            "entries_written": kept,
            "corpus_entries": len(existing),
            "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "tools": {
                name: {
                    "callable": bool(f.working),
                    "callable_bare": bool(f.working) and not f.requires_args,
                    "working_arguments": f.working,
                    "preconditions": f.preconditions,
                    "requires_args": f.requires_args,
                    "errors": f.errors[:3],
                }
                for name, f in findings.items()
            },
        }
        with open(args.report, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, sort_keys=True)
        if verbose:
            print(f"report            : {args.report}")

    return 0 if working else 1


if __name__ == "__main__":
    sys.exit(main())
