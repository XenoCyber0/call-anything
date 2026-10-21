# Playbook

How to work with the Call-Anything corpus.

| page | what it covers |
|---|---|
| [harness.md](harness.md) | Probing a live server and generating a corpus from it |
| [profile.md](profile.md) | Measuring how hard a tool is to call, from its schema alone |
| [lookup.md](lookup.md) | Finding a verified invocation efficiently |
| [record.md](record.md) | Adding entries without lying about them |
| [stale.md](stale.md) | What to do when a verified call no longer works |

## The one rule

**An entry without execution evidence is a claim, not a verified call.**

Everything else in this project follows from it: the `verified_by` ladder,
the CI gate, the refusal to serve claims by default, and the test that
forges an entry and checks the build fails.
