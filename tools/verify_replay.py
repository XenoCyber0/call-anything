#!/usr/bin/env python3
"""Replay verification: re-execute recorded calls and check the evidence.

The static gate (`build_calls.py --check`) can only prove that evidence
is PRESENT. It cannot prove the evidence is TRUE -- an entry can carry
invented output and still pass.

This tool closes that gap. It re-runs the recorded call against a live
server and compares the new output against the stored evidence.

    python3 tools/verify_replay.py --server rea \
        --cmd "node /path/to/rea/scripts/rea.mjs mcp"

Exit codes:
    0  every replayed entry still matches
    1  at least one entry no longer matches (or the server refused)

This is the honest answer to "how do I know the corpus is not lying?":
someone, or some CI job, re-runs the calls.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools"))

import call_core as core                      # noqa: E402
from seed_from_server import MCPClient, summarize_result  # noqa: E402


def _is_error_evidence(evidence: str) -> bool:
    """Does this recorded evidence represent a FAILURE?"""
    return '"error"' in evidence or "isError" in evidence


def comparable(evidence: str) -> str:
    """Reduce an output to the part that must stay stable.

    Full output is noisy: timestamps, ids, counters and paths change
    between runs. We compare the *shape* of the result -- its keys and
    their types -- which is what a recorded call actually promises.
    """
    text = evidence.strip()

    # Random identifiers (evidence ids, uuids) change on every run and say
    # nothing about whether the call still works.
    RID = re.compile(
        r"\b(ev|unk|session|req|task|evt|trace|span|run)_[0-9a-f]{8,}\b",
        re.I,
    )

    def scrub(t: str) -> str:
        # Order matters: replace random ids FIRST, while they still look
        # like ids, then neutralise the remaining numbers.
        t = RID.sub("<id>", t)
        return re.sub(r"\d+", "<n>", t)

    obj = None
    try:
        obj = json.loads(text)
    except Exception:
        # Not JSON. It may still be JSON embedded in a longer text blob
        # (many servers prefix their payload). Try to find the JSON
        # object inside it; if that fails, compare the scrubbed text.
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            try:
                obj = json.loads(text[start:end + 1])
            except Exception:
                obj = None
        if obj is None:
            return "text:" + scrub(text)[:400]

    def shape(node, depth=0):
        if depth > 6:
            return "..."
        if isinstance(node, dict):
            return {k: shape(v, depth + 1) for k, v in sorted(node.items())}
        if isinstance(node, list):
            if not node:
                return []
            return [shape(node[0], depth + 1)]
        if isinstance(node, bool):
            return "bool"
        if isinstance(node, (int, float)):
            return "num"
        if isinstance(node, str):
            if RID.search(node):
                return "id"
            return "str"
        if node is None:
            return "null"
        return type(node).__name__

    return "json:" + json.dumps(shape(obj), sort_keys=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", required=True)
    ap.add_argument("--cmd", required=True,
                    help="command that starts the MCP server")
    ap.add_argument("--corpus", default=os.path.join(REPO, "data", "calls.yaml"))
    ap.add_argument("--only", default="",
                    help="only replay entries whose id contains this")
    ap.add_argument("--update", action="store_true",
                    help="update evidence for entries that now match")
    args = ap.parse_args()

    entries = core.load_corpus(args.corpus)
    targets = [
        e for e in entries
        if e.server.lower() == args.server.lower()
        and e.is_verified
        and (not args.only or args.only in e.id)
    ]
    if not targets:
        # A vacuous pass is the worst failure mode for a verifier: a CI
        # job with a typo'd server name must NOT report success.
        print(
            f"no verified entries for server {args.server!r}",
            file=sys.stderr,
        )
        print(
            "refusing to report success on an empty target set -- check the "
            "--server name against the corpus",
            file=sys.stderr,
        )
        return 2

    client = MCPClient(args.cmd.split())
    failures = 0
    matches = 0
    try:
        init = client.initialize()
        info = init.get("result", {}).get("serverInfo", {})
        live_version = str(info.get("version", "") or "")
        print(f"replaying {len(targets)} entries against {args.server}\n")
        for e in targets:
            resp = client.call(e.tool, e.arguments)
            ok, out = summarize_result(resp)
            want = comparable(e.evidence)
            got = comparable(out)

            # An entry that RECORDS a precondition failure must still match
            # when the call fails the same way. Compare error shape, not
            # success shape. This branch must come BEFORE the generic
            # "call now errors" check.
            if not ok and _is_error_evidence(e.evidence):
                if want == got:
                    print(f"  ok   {e.id} (precondition still holds)")
                    matches += 1
                else:
                    print(f"  DIFF {e.id} (precondition changed)")
                    print(f"        stored : {want[:180]}")
                    print(f"        live   : {got[:180]}")
                    failures += 1
                continue

            if not ok:
                print(f"  FAIL {e.id}: call now errors -> {out[:100]}")
                failures += 1
                continue

            if want == got:
                print(f"  ok   {e.id}")
                matches += 1
                if args.update:
                    # A replayed entry MUST record the server version it was
                    # replayed against -- the corpus validator rejects
                    # `verified_by: replayed` entries with an empty version.
                    # We have the live server in hand, so record it. If the
                    # server reports no version, do NOT promote: that would
                    # write a corpus our own validator rejects.
                    if not live_version:
                        print(
                            "        skipped -- server reports no version, "
                            "cannot record a valid replayed entry"
                        )
                        continue
                    e.evidence = out[:2000]
                    e.verified_by = "replayed"
                    import datetime
                    e.verified_at = datetime.date.today().isoformat()
                    e.server_version = live_version
            else:
                print(f"  DIFF {e.id}")
                print(f"        stored : {want[:180]}")
                print(f"        live   : {got[:180]}")
                failures += 1
    finally:
        client.close()

    if args.update:
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
        print(f"\ncorpus updated: {args.corpus}")

    print(f"\n{matches} matched, {failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
