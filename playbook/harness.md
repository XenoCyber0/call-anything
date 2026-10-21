# The harness

The harness probes a live MCP server and writes a corpus of verified
calls. It is the mechanism by which the corpus grows without anyone
hand-writing anything.

```bash
python3 tools/harness.py \
  --server my-server \
  --cmd "node ./dist/index.js" \
  --out calls.yaml \
  --report harness-report.json
```

## What it does, in order

1. **Connect.** Launch the server, complete the MCP handshake, read
   `serverInfo` (for the version) and `tools/list`.
2. **Plan.** For each tool, build a bounded list of candidate argument
   sets. See "Where the candidates come from" below.
3. **Probe.** Call the tool with each candidate, in order, until one
   works.
4. **Classify.** Each attempt is one of: `ok`, `precondition`,
   `needs_args`, `other`.
5. **Record.** Emit a corpus entry for every tool that was proven
   callable, and for every tool whose failure was a *precondition*.
6. **Report.** Write a JSON report of the whole run.

## Where the candidates come from

Purely from the server's own schema, plus a tiny built-in list. No model.

For each argument, in priority order:

1. `const`
2. `default`
3. `examples` / `example`
4. `enum` values
5. type-appropriate common values (`"example"`, `"test"`, `"default"`,
   `"main"`, `"app"`, `"sample"`, `"1"` for strings; `0`, `1` for
   numbers; `true`, `false` for booleans)

Values that are almost never valid (`""`, `"null"`, `"undefined"`,
`"none"`, `"todo"`, `"changeme"`) are dropped.

The probe order is: bare call first, then each required argument alone
(which isolates *which* one matters), then the full required set, then
optional arguments one at a time.

## What gets recorded

| outcome | entry |
|---|---|
| the call succeeded | `verified_by: recorded`, `evidence` = the real output |
| the call failed with a precondition | `verified_by: recorded`, `evidence` = the **raw** error payload, tagged `precondition` |
| the call failed some other way | **nothing** — we do not record guesses |

The precondition entry stores the *raw* error, not a summary, so the
replay verifier can compare it shape-for-shape later.

## Preconditions are the valuable ones

A tool that fails with `target_unavailable` is teaching the agent an
ordering: *open the target first*. That is knowledge a schema cannot
express and an agent cannot guess.

On REA, 101 of 210 tools failed with a precondition. That is the
corpus's real content: the order of operations for a 210-tool
investigation surface.

## Limits — read this

- **The probe is bounded.** `--max-probes` (default 12) caps candidates
  per tool. A tool that needs a specific real path will not be found.
- **A successful call is not a complete answer.** The harness proves
  *a* working call exists, not that the call is safe or idempotent.
  The harness may create files, spawn processes, or write to the
  server's state. **Run it against a disposable environment.**
- **It does not know your semantics.** A call that returns
  `{"error": ...}` inside a 200 response counts as a failure; a call
  that returns garbage counts as a success if the transport says so.
- **Unproven is a normal outcome.** On REA, 100 of 210 tools were
  unproven. That is honest, not a bug.

## The report

```json
{
  "server": "rea",
  "server_version": "6.7.0",
  "tools_advertised": 210,
  "tools_callable": 9,
  "tools_precondition_only": 101,
  "tools_unproven": 100,
  "entries_written": 110,
  "tools": {
    "list_unknowns": {
      "callable": true,
      "callable_bare": true,
      "working_arguments": [{}],
      "preconditions": [],
      "requires_args": [],
      "errors": []
    }
  }
}
```

This is a **conformance report**. A server author can put it in their
README or their CI artifacts, and a reviewer can see exactly which
tools are usable and which need a precondition.

## Exit codes

| code | meaning |
|---|---|
| 0 | at least one working call was found |
| 1 | no working calls, or the server could not be reached |
| 2 | the server exposed no tools |
