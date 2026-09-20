# Self-healing work: handoff and review checklist

Written 2026-09-16 after three commits (`f574d59`, `1d3b1a7`, the lock-split commit) on `main`. Neither
was exercised against the live Anthropic API; the unit suite (282 tests)
passes. Review these with a second model before deploying to the Optiplex.

## What shipped

1. **Self-diagnosis** (`brain/tool_health.py`, 1.27 / build 29)
   - Every `execute_tool` call is scored. Raised exception, a `Tool error`
     reply, or an outage phrase ("not configured", "unreachable", "timed
     out", HTTP 401/403/5xx) counts as a failure.
   - Two failures in a row, or four inside an hour, mark a tool degraded.
   - Degraded tools are injected into the dynamic system block as a
     "Systems health" section (empty and free when healthy).
   - Tools: `system_health`, `clear_tool_health`.
   - The Home Assistant live monitor reports as `home_assistant_monitor`.
   - `brain/agent.py` reads `ANTHROPIC_MODEL` / `ANTHROPIC_API_KEY` live.
2. **Governed self-repair** (`brain/self_repair.py`, 1.28 / build 30)
   - `diagnose_tool` -> playbook lookup, `.env` key check, TCP probe, one
     staged proposal with an id in `memory/repair_proposals.json`.
   - `apply_repair(proposal_id, confirmed=true, value?)` runs one of:
     `set_setting` (validated, auto-rollback if still unreachable),
     `run_command` (name from `SELF_REPAIR_COMMANDS` only), `clear_flag`.
   - `list_repair_proposals`, `reject_repair`.
   - New editable settings: `ANTHROPIC_MODEL`, `SELF_REPAIR_COMMANDS`.
3. **Lesson capture** (`brain/memory_extractor.py`)
   - Extraction schema gained `corrections`; directives at confidence >= 0.85
     not already covered become standing orders automatically.

## Review checklist

- [ ] `brain/self_repair.py` `apply()` `run_command` branch: commands with
      shell metacharacters run via `/bin/sh -c`. Commands come only from
      `.env`, but confirm that is acceptable.
- [ ] Rollback only restores a setting that previously had a value. If the
      key was unset before the repair, the new value stays. Decide.
- [ ] `_CORRECTION_THRESHOLD = 0.85` in the extractor. Watch
      `memory/standing_orders.json` for junk orders for a few days.
- [ ] `tests/test_runtime_monitors.py` now writes to the real
      `memory/tool_health.json` (gitignored). Patch `tool_health._FILE` in
      that test's setUp for cleanliness.
- [ ] `tool_health.looks_like_failure` only inspects the first 200 chars of
      a reply. Check it does not misfire on legitimate replies that begin
      with "unauthorized" or similar words.
- [ ] Prompt-cache impact: the health section lives in the uncached dynamic
      block, so no cache invalidation. Confirm nothing was added to the
      persona block.

## Deploy steps (Optiplex)

```bash
ssh optiplex
cd ~/jarvis && git pull
# add to .env, adjust names/commands to taste:
# Do not add NAS Docker/SSH commands to SELF_REPAIR_COMMANDS.
# Arr recovery uses the dedicated restricted key and per-request confirmation;
# see ops/nas/README.md and PROGRESS.md for installation/activation status.
# restart the JARVIS process as usual
```

## Manual smoke test

1. `python -m app.main --text`, ask "how are your systems?" -> `system_health`.
2. Set a wrong `SPOTIFY_CLIENT_SECRET`, ask for music twice, ask again ->
   the Systems health section should make him say Spotify is down.
3. "Fix yourself" -> `diagnose_tool` stages a proposal; "yes, apply it" ->
   `apply_repair` with the id.
4. Tell him "stop saying 'certainly'" -> within a minute a "Lesson learned"
   event and a new line in `memory/standing_orders.json`.

4. **Runtime lock split** (`brain/runtime.py`, 1.29 / build 31)
   - `_turn_lock` serializes turns and session switches; `_lock` guards state
     only and is never held across a model call.
   - Review: `respond_streaming` now holds `_turn_lock` while the consumer
     iterates. Both GUI and voice consume promptly on one worker thread, but
     a stalled consumer would block companion turns until it finishes.

## Next in value order

1. Source-level self-modification on the same proposal pattern: patch,
   test, approve, activate, rollback.
