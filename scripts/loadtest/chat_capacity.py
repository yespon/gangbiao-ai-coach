#!/usr/bin/env python3
"""Concurrent capacity load test for chat endpoints.

Scenarios:
- chat:      /sessions + /chat
- stream:    /sessions + /chat/stream (SSE)
- attachment:/sessions + /chat with file upload

Auth flow:
1) POST /auth/login
2) Reuse returned cookies for subsequent write requests.
3) Send CSRF header (X-CSRF-Token) with value from csrf cookie.

Example:
  python scripts/loadtest/chat_capacity.py \
    --base-url http://127.0.0.1:2088/api/v1 \
    --email loadtest@example.com \
    --password 'YourPass123!' \
    --scenario all \
    --concurrency 20 \
    --requests 200 \
    --attachment-path tests/fixtures/classifier/cases/sample.xlsx
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx


@dataclass
class Threshold:
    p95_ms: float
    error_rate_max: float
    first_token_p95_ms: float | None = None


THRESHOLDS: dict[str, Threshold] = {
    "chat": Threshold(p95_ms=8000.0, error_rate_max=0.01),
    "stream": Threshold(p95_ms=20000.0, error_rate_max=0.01, first_token_p95_ms=4000.0),
    "attachment": Threshold(p95_ms=15000.0, error_rate_max=0.03),
}


@dataclass
class RequestResult:
    ok: bool
    status_code: int
    latency_ms: float
    first_token_ms: float | None = None
    error: str | None = None


def percentile(values: list[float], q: float) -> float:
    if not values:
        return math.nan
    if len(values) == 1:
        return values[0]
    values_sorted = sorted(values)
    pos = (len(values_sorted) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return values_sorted[lo]
    frac = pos - lo
    return values_sorted[lo] * (1.0 - frac) + values_sorted[hi] * frac


async def login(base_url: str, email: str, password: str, timeout_s: float) -> dict[str, str]:
    async with httpx.AsyncClient(timeout=timeout_s) as client:
        resp = await client.post(
            f"{base_url}/auth/login",
            json={"email": email, "password": password},
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"login failed: {resp.status_code} {resp.text}")

        cookie_jar = dict(resp.cookies)
        csrf = cookie_jar.get("csrf_token")
        if not csrf:
            raise RuntimeError("csrf_token cookie not found after login")
        return cookie_jar


async def create_session(
    client: httpx.AsyncClient,
    base_url: str,
    csrf_token: str,
) -> str:
    resp = await client.post(
        f"{base_url}/sessions",
        json={"show_context_in_history": True},
        headers={"X-CSRF-Token": csrf_token},
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"create session failed: {resp.status_code} {resp.text}")
    data = resp.json()
    sid = data.get("session_id")
    if not sid:
        raise RuntimeError("missing session_id in create session response")
    return sid


async def run_chat_once(
    client: httpx.AsyncClient,
    base_url: str,
    csrf_token: str,
    message: str,
) -> RequestResult:
    start = time.perf_counter()
    try:
        sid = await create_session(client, base_url, csrf_token)
        resp = await client.post(
            f"{base_url}/chat",
            data={"session_id": sid, "message": message},
            headers={"X-CSRF-Token": csrf_token},
        )
        latency_ms = (time.perf_counter() - start) * 1000.0
        if resp.status_code >= 400:
            return RequestResult(False, resp.status_code, latency_ms, error=resp.text)
        return RequestResult(True, resp.status_code, latency_ms)
    except Exception as exc:  # noqa: BLE001
        latency_ms = (time.perf_counter() - start) * 1000.0
        return RequestResult(False, 0, latency_ms, error=str(exc))


async def run_stream_once(
    client: httpx.AsyncClient,
    base_url: str,
    csrf_token: str,
    message: str,
) -> RequestResult:
    start = time.perf_counter()
    first_token_ms: float | None = None
    got_data = False
    try:
        sid = await create_session(client, base_url, csrf_token)
        async with client.stream(
            "POST",
            f"{base_url}/chat/stream",
            data={"session_id": sid, "message": message},
            headers={"X-CSRF-Token": csrf_token},
        ) as resp:
            if resp.status_code >= 400:
                latency_ms = (time.perf_counter() - start) * 1000.0
                body = await resp.aread()
                return RequestResult(False, resp.status_code, latency_ms, error=body.decode("utf-8", "ignore"))

            async for line in resp.aiter_lines():
                if not line:
                    continue
                if line.startswith("data:"):
                    got_data = True
                    if first_token_ms is None:
                        first_token_ms = (time.perf_counter() - start) * 1000.0

        latency_ms = (time.perf_counter() - start) * 1000.0
        if not got_data:
            return RequestResult(False, 200, latency_ms, first_token_ms, error="no sse data")
        return RequestResult(True, 200, latency_ms, first_token_ms)
    except Exception as exc:  # noqa: BLE001
        latency_ms = (time.perf_counter() - start) * 1000.0
        return RequestResult(False, 0, latency_ms, first_token_ms, error=str(exc))


async def run_attachment_once(
    client: httpx.AsyncClient,
    base_url: str,
    csrf_token: str,
    message: str,
    attachment_path: Path,
) -> RequestResult:
    start = time.perf_counter()
    try:
        sid = await create_session(client, base_url, csrf_token)
        file_bytes = attachment_path.read_bytes()
        files = {
            "files": (
                attachment_path.name,
                file_bytes,
                "application/octet-stream",
            )
        }
        resp = await client.post(
            f"{base_url}/chat",
            data={"session_id": sid, "message": message},
            files=files,
            headers={"X-CSRF-Token": csrf_token},
        )
        latency_ms = (time.perf_counter() - start) * 1000.0
        if resp.status_code >= 400:
            return RequestResult(False, resp.status_code, latency_ms, error=resp.text)
        return RequestResult(True, resp.status_code, latency_ms)
    except Exception as exc:  # noqa: BLE001
        latency_ms = (time.perf_counter() - start) * 1000.0
        return RequestResult(False, 0, latency_ms, error=str(exc))


async def run_scenario(
    *,
    scenario: str,
    base_url: str,
    email: str,
    password: str,
    concurrency: int,
    requests: int,
    timeout_s: float,
    message: str,
    attachment_path: Path | None,
) -> dict[str, Any]:
    if requests <= 0:
        raise ValueError("requests must be > 0")
    if concurrency <= 0:
        raise ValueError("concurrency must be > 0")
    if scenario == "attachment" and attachment_path is None:
        raise ValueError("attachment scenario requires --attachment-path")

    cookies = await login(base_url, email, password, timeout_s)
    csrf_token = cookies.get("csrf_token", "")

    counter = {"next": 0}
    lock = asyncio.Lock()
    results: list[RequestResult] = []

    async def next_index() -> int | None:
        async with lock:
            idx = counter["next"]
            if idx >= requests:
                return None
            counter["next"] = idx + 1
            return idx

    async def worker(worker_id: int) -> None:
        _ = worker_id
        async with httpx.AsyncClient(
            timeout=timeout_s,
            cookies=cookies,
            limits=httpx.Limits(max_connections=concurrency * 2, max_keepalive_connections=concurrency),
        ) as client:
            while True:
                idx = await next_index()
                if idx is None:
                    return
                if scenario == "chat":
                    res = await run_chat_once(client, base_url, csrf_token, message)
                elif scenario == "stream":
                    res = await run_stream_once(client, base_url, csrf_token, message)
                elif scenario == "attachment":
                    assert attachment_path is not None
                    res = await run_attachment_once(client, base_url, csrf_token, message, attachment_path)
                else:
                    raise ValueError(f"unknown scenario: {scenario}")
                results.append(res)

    started = time.perf_counter()
    await asyncio.gather(*[worker(i) for i in range(concurrency)])
    elapsed_s = time.perf_counter() - started

    latencies = [r.latency_ms for r in results]
    first_tokens = [r.first_token_ms for r in results if r.first_token_ms is not None]
    ok_count = sum(1 for r in results if r.ok)
    err_count = len(results) - ok_count
    error_rate = (err_count / len(results)) if results else 1.0

    stats = {
        "scenario": scenario,
        "concurrency": concurrency,
        "requests": len(results),
        "ok": ok_count,
        "errors": err_count,
        "error_rate": error_rate,
        "elapsed_s": elapsed_s,
        "rps": (len(results) / elapsed_s) if elapsed_s > 0 else math.nan,
        "latency_ms": {
            "avg": statistics.mean(latencies) if latencies else math.nan,
            "p50": percentile(latencies, 0.50),
            "p90": percentile(latencies, 0.90),
            "p95": percentile(latencies, 0.95),
            "p99": percentile(latencies, 0.99),
            "max": max(latencies) if latencies else math.nan,
        },
        "first_token_ms": {
            "p50": percentile(first_tokens, 0.50) if first_tokens else math.nan,
            "p95": percentile(first_tokens, 0.95) if first_tokens else math.nan,
        },
    }

    threshold = THRESHOLDS[scenario]
    gate_pass = True
    gate_reason: list[str] = []

    if stats["latency_ms"]["p95"] > threshold.p95_ms:
        gate_pass = False
        gate_reason.append(
            f"latency p95 {stats['latency_ms']['p95']:.1f}ms > {threshold.p95_ms:.1f}ms"
        )

    if stats["error_rate"] > threshold.error_rate_max:
        gate_pass = False
        gate_reason.append(
            f"error_rate {stats['error_rate']:.3%} > {threshold.error_rate_max:.3%}"
        )

    if threshold.first_token_p95_ms is not None:
        p95_ft = stats["first_token_ms"]["p95"]
        if not math.isnan(p95_ft) and p95_ft > threshold.first_token_p95_ms:
            gate_pass = False
            gate_reason.append(
                f"first_token p95 {p95_ft:.1f}ms > {threshold.first_token_p95_ms:.1f}ms"
            )

    stats["gate"] = {
        "pass": gate_pass,
        "reason": gate_reason,
        "threshold": {
            "p95_ms": threshold.p95_ms,
            "error_rate_max": threshold.error_rate_max,
            "first_token_p95_ms": threshold.first_token_p95_ms,
        },
    }

    return stats


def print_summary(stats: dict[str, Any]) -> None:
    print(f"\\n=== Scenario: {stats['scenario']} ===")
    print(
        "requests={requests} concurrency={concurrency} ok={ok} errors={errors} "
        "error_rate={error_rate:.2%} elapsed={elapsed_s:.2f}s rps={rps:.2f}".format(**stats)
    )
    lm = stats["latency_ms"]
    print(
        "latency_ms avg={avg:.1f} p50={p50:.1f} p90={p90:.1f} p95={p95:.1f} p99={p99:.1f} max={max:.1f}".format(
            **lm
        )
    )
    ft = stats["first_token_ms"]
    if not math.isnan(ft["p95"]):
        print(f"first_token_ms p50={ft['p50']:.1f} p95={ft['p95']:.1f}")

    gate = stats["gate"]
    if gate["pass"]:
        print("gate=PASS")
    else:
        print("gate=FAIL")
        for reason in gate["reason"]:
            print(f"- {reason}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Chat service concurrency load test")
    parser.add_argument("--base-url", default="http://127.0.0.1:2088/api/v1")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--scenario", choices=["chat", "stream", "attachment", "all"], default="all")
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--requests", type=int, default=200)
    parser.add_argument("--timeout-s", type=float, default=180.0)
    parser.add_argument("--message", default="Please summarize this requirement in 3 bullets.")
    parser.add_argument("--attachment-path", default="")
    parser.add_argument("--report", default="reports/chat_capacity_report.json")
    return parser.parse_args()


async def async_main() -> int:
    args = parse_args()
    attachment_path = Path(args.attachment_path) if args.attachment_path else None

    scenarios = [args.scenario] if args.scenario != "all" else ["chat", "stream", "attachment"]
    outputs: list[dict[str, Any]] = []

    for s in scenarios:
        stats = await run_scenario(
            scenario=s,
            base_url=args.base_url.rstrip("/"),
            email=args.email,
            password=args.password,
            concurrency=args.concurrency,
            requests=args.requests,
            timeout_s=args.timeout_s,
            message=args.message,
            attachment_path=attachment_path,
        )
        print_summary(stats)
        outputs.append(stats)

    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps({"results": outputs}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\\nreport written to: {report_path}")

    all_pass = all(item["gate"]["pass"] for item in outputs)
    return 0 if all_pass else 2


def main() -> None:
    code = asyncio.run(async_main())
    raise SystemExit(code)


if __name__ == "__main__":
    main()
