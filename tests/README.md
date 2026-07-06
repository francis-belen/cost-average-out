# Tests

Test coverage uses fake exchange adapters and deterministic planner, ledger,
cycle, presentation, reconciliation, notification, and CLI cases. Real exchange
integration tests must be opt-in and must never run by default.

Run the test suite with:

```bash
.venv/bin/pytest -q
```
