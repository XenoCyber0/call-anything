#!/usr/bin/env bash
# Live demo of Call-Anything: the corpus of VERIFIED working tool invocations.
set -u
cd "$(dirname "$0")/.."

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
cp -r data tools "$WORK"/
cd "$WORK"

BOLD=$'\033[1m'; CYAN=$'\033[1;36m'; GRN=$'\033[0;32m'
RED=$'\033[0;31m'; DIM=$'\033[0;90m'; YEL=$'\033[1;33m'; OFF=$'\033[0m'

banner() { printf '\n%s%s%s\n' "$CYAN" "$*" "$OFF"; }
step()   { printf '\n%s== %s%s\n' "$YEL" "$*" "$OFF"; }
note()   { printf '%s%s%s\n' "$DIM" "$*" "$OFF"; }
good()   { printf '%s%s%s\n' "$GRN" "$*" "$OFF"; }
bad()    { printf '%s%s%s\n' "$RED" "$*" "$OFF"; }

python3 - <<'PY'
import json, subprocess, sys

def rpc(mcp, method, params=None, _id=[0]):
    _id[0] += 1
    p = subprocess.Popen([sys.executable, mcp], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                         text=True, bufsize=1)
    p.stdin.write(json.dumps({"jsonrpc":"2.0","id":_id[0],"method":method,
                              "params":params or {}})+"\n")
    p.stdin.flush()
    r = None
    while True:
        line = p.stdout.readline()
        if not line: break
        try:
            r = json.loads(line); break
        except Exception: continue
    p.terminate()
    return r

def call(mcp, name, args):
    r = rpc(mcp, "tools/call", {"name": name, "arguments": args})
    txt = "".join(c.get("text","") for c in r["result"]["content"])
    return json.loads(txt)

MCP = "tools/mcp/call_mcp.py"
print("MCP server:", MCP)
PY

banner "Call-Anything -- verified working MCP tool invocations"

step "1. The corpus is a set of calls that were REALLY executed"
python3 -c "
import sys; sys.path.insert(0,'tools')
import call_core as c
es = c.load_corpus('data/calls.yaml')
print(f'  {len(es)} entries, {sum(1 for e in es if e.is_verified)} carry execution evidence')
for e in es[:3]:
    print(f'    {e.tool:24s} {e.verified_by:9s} {e.verified_at}  {e.summary[:44]}')
"

step "2. Ask for a verified call instead of guessing the arguments"
python3 - <<'PY'
import json, subprocess, sys
p = subprocess.Popen([sys.executable,"tools/mcp/call_mcp.py"], stdin=subprocess.PIPE,
    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
def send(o): p.stdin.write(json.dumps(o)+"\n"); p.stdin.flush()
def read():
    while True:
        l = p.stdout.readline()
        if not l: return None
        try: return json.loads(l)
        except: continue
send({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"d","version":"1"}}}); read()
send({"jsonrpc":"2.0","method":"notifications/initialized"})
send({"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"find_call","arguments":{"tool":"list_unknowns"}}}); r=read()
d = json.loads(r["result"]["content"][0]["text"])
c = d["calls"][0]
print(f"  found {d['count']} verified call(s) for list_unknowns")
print(f"    arguments : {json.dumps(c['arguments'])}")
print(f"    evidence  : {c['evidence'][:76]}")
print(f"    verified  : {c['verified_by']} on {c['verified_at']} against {c['server_version']}")
p.terminate()
PY

step "3. The schema alone does NOT tell you the right arguments"
python3 - <<'PY'
import sys, json; sys.path.insert(0,'tools')
from call_profile import profile_tool
# A realistic ambiguous tool
schema = {"name":"analyze_binary","inputSchema":{"type":"object","properties":{
  "mode":{"type":"string","enum":["fast","safe","strict","debug","audit","raw"]},
  "depth":{"type":"string","enum":["shallow","full","deep"]},
  "target":{"type":"string"}},"required":["mode","target"]}}
p = profile_tool(schema)
print(f"  required      : {p.required}")
print(f"  free-text req : {p.free_text_required}")
print(f"  guessing cost : {p.total_guessing_cost} wrong answers the schema leaves open")
print(f"  difficulty    : {p.difficulty()}")
print(f"  unguessable   : {p.unguessable}")
PY

step "4. A preconditon is knowledge too: what must happen FIRST"
python3 - <<'PY'
import sys; sys.path.insert(0,'tools')
import call_core as c
es = c.load_corpus('data/calls.yaml')
pre = [e for e in es if "precondition" in e.tags]
print(f"  {len(pre)} entries record a precondition, e.g.:")
e = pre[0]
print(f"    {e.tool}: {e.summary}")
print(f"    evidence: {e.evidence[:76]}")
PY

step "5. The gate rejects an entry that claims to be verified with NO evidence"
python3 - <<'PY'
import yaml, subprocess, sys
d = yaml.safe_load(open("data/calls.yaml"))
d["calls"].append({"id":"forged","server":"rea","tool":"list_architectures",
  "arguments":{},"summary":"Totally works, trust me","evidence":"",
  "verified_by":"recorded","verified_at":"2026-10-21","server_version":"6.7.0",
  "tags":["rea"],"notes":""})
yaml.safe_dump(d, open("data/calls.yaml","w"), sort_keys=False, allow_unicode=True, default_flow_style=False, width=100)
r = subprocess.run([sys.executable,"tools/build_calls.py","--check"], capture_output=True, text=True)
print(f"  exit code: {r.returncode}")
for line in r.stderr.strip().splitlines():
    print(f"  {line}")
PY

step "6. But the static gate CANNOT catch INVENTED evidence -- so we replay"
python3 - <<'PY'
import yaml, subprocess, sys
d = yaml.safe_load(open("data/calls.yaml"))
d["calls"] = [c for c in d["calls"] if c["id"] != "forged"]
d["calls"].append({"id":"forged2","server":"rea","tool":"list_architectures",
  "arguments":{},"summary":"Invented result","evidence":
  '{"result": {"architectures": ["x86_64","aarch64"], "count": 2}}',
  "verified_by":"recorded","verified_at":"2026-10-21","server_version":"6.7.0",
  "tags":["rea"],"notes":""})
yaml.safe_dump(d, open("data/calls.yaml","w"), sort_keys=False, allow_unicode=True, default_flow_style=False, width=100)
r = subprocess.run([sys.executable,"tools/build_calls.py","--check"], capture_output=True, text=True)
corpus_ok = "CORPUS INVALID" not in r.stderr
print(f"  corpus validity : {'PASSES' if corpus_ok else 'REJECTED'}"
      f"   (the invented evidence IS present, so the static gate cannot object)")
print("  -> only a LIVE REPLAY can prove the evidence is true")
PY

step "7. Live replay: re-execute every recorded call and compare"
REA_MJS="${REA_MJS:-/path/to/rea/scripts/rea.mjs}"
if [ -f "$REA_MJS" ]; then
  python3 tools/verify_replay.py --server rea --cmd "node $REA_MJS mcp" 2>&1 | tail -18
else
  echo "  (rea not installed; skipping live replay)"
fi

step "8. The whole test suite"
python3 -m unittest discover -s tools -p "test_*.py" 2>&1 | tail -3

banner "Done"
note "  6 tools, ~790 tokens of schema, no model, no API key, no network."
