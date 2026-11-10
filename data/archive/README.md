# Session archive

This folder stores a **manifest** of every Codex session log found on the machine
(`~/.codex/sessions` and `~/.codex/archived_sessions`). It is a backup of the
derived numbers, not of the raw transcripts.

## Why not the raw logs

The raw logs are ~2.7 GB on disk (652 archived + ~400 live files, zstd-compressed).
GitHub rejects individual files over 100 MB and discourages repositories over 1 GB,
so the transcripts cannot be stored here.

## What is stored

| File | Content |
| --- | --- |
| `sessions.json` | One record per session file: name, origin, size, SHA-256, event count, first/last timestamp, active seconds, added/removed lines, token usage, model line counts |
| `daily.json` | Per-day rollup: sessions, active seconds, added lines, input/output tokens |

Each record carries the **SHA-256 of the original file**, so if the raw log still
exists locally it can be verified against the manifest.

## How the numbers are computed

- **active_seconds**: timestamps of all events in a session are sorted; consecutive
  gaps are summed, each capped at 15 minutes.
- **added / removed**: line counts from `FileChange` unified diffs.
- **tokens**: `token_count` events, `last_token_usage` per turn.
- **models**: added lines attributed to the model active at that moment.

## Caveats

- `active_seconds` is a **naive sum across sessions**. Sessions that ran concurrently
  are counted multiple times, so the total is an upper bound, not a wall-clock figure.
- Token counts are per-turn usage; repeated context is counted once per turn.
- Hand-written lines are not recorded anywhere in the logs, so they cannot be measured.
