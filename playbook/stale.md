# When a verified call stops working

A verified call can go stale. Servers change their schemas, rename tools,
or alter their output. The corpus has to be able to say so honestly.

## Symptoms

- The tool is not in the server's `tools/list` any more.
- The call is rejected: unknown tool, unknown argument, validation error.
- The call succeeds but the output no longer looks like the recorded
  evidence.

## What to do

1. **Do not silently work around it.** The next agent will hit the same
   wall, and the corpus still claims the call works.
2. **Re-verify.** Run the call again with the same arguments.
3. **If it still works**, upgrade the entry: `verified_by: replayed`, bump
   `verified_at`, and note the server version you replayed against. A
   `replayed` entry with a fresh date is worth more than a `recorded` one
   from last month.
4. **If it does not work**, find the call that does, and record that
   instead. Add a note to the old entry explaining the change.
5. **If the tool is gone**, remove the entry. A corpus that keeps dead
   entries is worse than an empty one, because agents trust it.

## Version drift is not staleness

If the `server_version` differs from the version your entry was recorded
against, the arguments are a *hint*. Try them. If they work, replay the
entry against your version. Do not delete a call for a version bump alone.

## The trust ladder again

| state | meaning | how to get there |
|---|---|---|
| `claimed` | asserted, unproven | someone wrote it down |
| `recorded` | executed once, succeeded | the seeder or `record_call` |
| `replayed` | executed again later, still succeeded | re-run it and bump the entry |
| `witnessed` | a human confirmed the outcome | someone ran it and said so |

`replayed` is the important one. A corpus where every entry is
`recorded` once and never revisited is a corpus that decays silently. The
upgrade path is a re-run, and the seeder is the tool for it.
