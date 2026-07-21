# Concurrency Capacity Playbook

This document contains:
- Step 1: practical load test plan with pass/fail gates.
- Step 2: prioritized concurrency optimization checklist mapped to code.

## 1) Load Test Plan (Step 1)

### 1.1 Preconditions

- Backend and gateway are reachable from the test machine.
- Local auth mode is enabled and a test account exists.
- You have a test attachment file for the attachment scenario.

Recommended target URL:
- http://127.0.0.1:2088/api/v1

### 1.2 Script

Use [scripts/loadtest/chat_capacity.py](scripts/loadtest/chat_capacity.py).

The script executes 3 scenarios:
- chat: create session + non-stream chat
- stream: create session + SSE chat
- attachment: create session + chat with one uploaded file

### 1.3 Example Commands

Run all scenarios:

```bash
python scripts/loadtest/chat_capacity.py \
  --base-url http://127.0.0.1:2088/api/v1 \
  --email loadtest@example.com \
  --password 'YourPass123!' \
  --scenario all \
  --concurrency 20 \
  --requests 200 \
  --attachment-path tests/fixtures/classifier/cases/sample.xlsx \
  --report reports/chat_capacity_report.json
```

Run only stream scenario:

```bash
python scripts/loadtest/chat_capacity.py \
  --base-url http://127.0.0.1:2088/api/v1 \
  --email loadtest@example.com \
  --password 'YourPass123!' \
  --scenario stream \
  --concurrency 30 \
  --requests 300
```

### 1.4 Capacity Sweep Method

Use fixed requests and grow concurrency by steps:
- 10, 20, 30, 40, 60, 80
- at each step run all scenarios
- collect p95, p99, error rate, RPS, stream first token p95

Capacity baseline is the highest concurrency step where all scenarios pass their gates.

### 1.5 Pass/Fail Gates

Default gates are implemented inside the script:
- chat: p95 <= 8000 ms, error rate <= 1%
- stream: p95 <= 20000 ms, first token p95 <= 4000 ms, error rate <= 1%
- attachment: p95 <= 15000 ms, error rate <= 3%

When any scenario fails, script exits with code 2.

### 1.6 Interpreting Results

- If stream first token p95 fails first:
  likely event loop or upstream LLM backpressure.
- If attachment p95 fails first:
  likely synchronous extraction cost or file IO contention.
- If error rate grows before p95:
  check upstream LLM limits, DB saturation, or timeouts.

## 2) Concurrency Optimization Checklist (Step 2)

This checklist is ordered by expected impact.

### P0: Scale app worker model

- Current command is single-process uvicorn in [Dockerfile.backend](Dockerfile.backend#L35).
- Action:
  - Move to multi-worker runtime (for example gunicorn + uvicorn workers).
  - Start from workers = CPU cores and tune with memory headroom.
- Why:
  - Isolates event loops and improves parallel request handling.

### P0: Move blocking attachment extraction off event loop

- Blocking points:
  - [app/extractors/manager.py](app/extractors/manager.py#L85)
  - [app/extractors/document.py](app/extractors/document.py#L36)
  - [app/extractors/document.py](app/extractors/document.py#L75)
  - [app/extractors/spreadsheet.py](app/extractors/spreadsheet.py#L444)
- Action:
  - Execute heavy extraction in thread/process pool or background queue.
  - Enforce strict file size/type limits per scenario.
- Why:
  - Prevents single request from stalling all async progress.

### P1: Reuse HTTP clients for LLM calls

- Current code creates a new AsyncClient per request in:
  - [app/services/llm_service.py](app/services/llm_service.py#L60)
  - [app/services/llm_service.py](app/services/llm_service.py#L91)
- Action:
  - Use app-lifespan scoped clients with explicit connection pool limits.
  - Add retry/backoff for transient upstream failures.
- Why:
  - Reduces connection setup overhead and improves tail latency.

### P1: Revisit DB pool and write hot path

- Pool config is currently in [app/core/database.py](app/core/database.py#L7).
- Message insert path computes max(seq) before insert in [app/services/message_service.py](app/services/message_service.py#L18).
- Action:
  - Raise pool after worker scaling based on DB capacity tests.
  - Replace max(seq)+1 pattern with safer sequence/constraint strategy.
- Why:
  - Avoids lock/contention amplification under concurrent writes.

### P1: Session auth write frequency

- Sliding refresh writes in [app/api/deps.py](app/api/deps.py#L60).
- Action:
  - Increase refresh interval or batch/defer updates.
- Why:
  - Reduces write amplification on hot authenticated paths.

### P2: Nginx timeouts and SSE tuning

- API proxy block in [deploy/nginx/default.conf](deploy/nginx/default.conf#L8).
- Action:
  - Tune proxy read timeout and buffering behavior for long SSE responses.
- Why:
  - Prevents edge disconnects under long stream durations.

## 3) Suggested Rollout Sequence

1. Apply P0 items first.
2. Re-run full capacity sweep.
3. Apply P1 items.
4. Re-run and compare report deltas.
5. Set production concurrency limits from the last passing step with 30% safety margin.
