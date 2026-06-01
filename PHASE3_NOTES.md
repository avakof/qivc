# Phase 3 Notes — Orchestration

## Acceptance gate results

| Gate | Result |
|---|---|
| `uv run pytest tests/integration -q` passes | **PASS** (4 tests) |
| `uv run qivc graph --output /tmp/graph.png` produces a file | **PASS** (46 KB PNG) |
| Run-audit log has an entry for every node on fixture data | **PASS** (asserted in `test_end_to_end_fcn_rejected_at_fscore`) |
| Kill-mid-run + resume completes | **PASS** (see below) |

## Deviation from the brief: `AsyncSqliteSaver` instead of `SqliteSaver`

The brief (Phase 3, item 3) specifies:

```python
from langgraph.checkpoint.sqlite import SqliteSaver
checkpointer = SqliteSaver.from_conn_string(settings.db_path)
```

**Problem:** every node in this pipeline is an `async def` coroutine (the data
agents are async, per the Phase 1 contract). The graph is therefore driven with
`graph.ainvoke(...)`. The synchronous `SqliteSaver` raises
`NotImplementedError: The SqliteSaver does not support async methods` when used
with `ainvoke`.

**Resolution:** use `langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver`, the async
variant of the *same* SQLite checkpointer. It satisfies the spec's stated intent
("SqliteSaver checkpointer for resumability — no Postgres dep") and uses the
already-present `aiosqlite` dependency. No new top-level dependency was added.

A separate `*_checkpoints.sqlite` file holds the LangGraph checkpoints; the
DuckDB file (`qivc.db`) holds the `run_audit` table. They are intentionally kept
in different stores (DuckDB has no async checkpointer; LangGraph ships a SQLite one).

## Checkpoint resumability — how it is verified

`tests/integration/test_resume.py` injects a deterministic mid-run failure:

1. `make_nodes` is patched so the `fetch_fundamentals` **node** raises
   `RuntimeError` on its first invocation.
2. `graph.ainvoke(initial_state, config={"thread_id": run_id})` is called; it
   raises. LangGraph has checkpointed every super-step that completed before the
   failure (regime_check, ingest_form4, and the sibling fetch_* nodes whose
   writes landed).
3. `graph.aget_state(config)` confirms `transactions_by_ticker` is persisted.
4. `graph.ainvoke(None, config=...)` re-runs with the **same** `thread_id` and a
   `None` input — LangGraph's signal to resume from the last checkpoint.
5. Assertions prove **resumption, not restart**:
   - `fetch_fundamentals` runs again (call_count 1 → 2) and succeeds.
   - `regime_check` and `ingest_form4` each appear **exactly once** in
     `run_audit` — they were not re-executed; their checkpointed writes were
     replayed.
   - The pipeline completes: ticker `RES` is a candidate.

### Manual process-kill verification

The automated test above injects an in-process exception. To verify a true
process kill (SIGKILL mid-run) and restart:

```bash
# Terminal 1 — start a screen, note the run_id printed at the top, then Ctrl-C
# (or kill -9 the PID) while the fetch_* nodes are running:
uv run qivc screen --ticker FCN

# The checkpoint DB persists at data/duckdb/qivc_checkpoints.sqlite.
# Re-run with the SAME run_id to resume (resume-by-run-id wiring is in
# cli/main.py: thread_id == run_id). On resume, completed nodes are skipped
# and the run continues from the last checkpointed super-step.
```

Because checkpoints are written to durable SQLite after each super-step, a hard
process kill loses only the in-flight super-step, which is re-executed on resume.
This was confirmed manually against fixture-backed agents; the automated
`test_resume.py` is the regression guard.

## Graph topology

```
START → regime_check → ingest_form4
      → [fan-out, parallel]
          fetch_insider_histories
          fetch_fundamentals
          fetch_valuation
          fetch_short_interest
          fetch_revisions
          fetch_liquidity
      → [join] apply_cluster_detection → apply_filters → apply_synthesis → END
```

`apply_synthesis` is a Phase 4 placeholder (returns `{}`); conviction scoring and
the risk overlay are added there.
