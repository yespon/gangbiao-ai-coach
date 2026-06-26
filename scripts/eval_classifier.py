#!/usr/bin/env python3
"""Evaluate the template classifier against labeled Excel cases.

Loads labeled .xlsx/.xls cases, runs each through the full classifier
pipeline (extract -> classify via real LLM), and reports accuracy,
per-class precision/recall/F1, macro-F1, and a confusion matrix.

Usage:
    python scripts/eval_classifier.py \
        --cases tests/fixtures/classifier/cases \
        --labels tests/fixtures/classifier/labels.json \
        --report reports/classifier_eval.json

labels.json format: {"<filename>": "D1".."D7" | "NONE"}
"""

import argparse
import asyncio
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

# Bootstrap project root onto sys.path so `app.*` imports work when run as a script.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.core.config import settings  # noqa: E402
from app.services.template_classifier import (  # noqa: E402
    classify_file,
    predicted_label,
)

LABELS = ["D1", "D2", "D3", "D4", "D5", "D6", "D7", "NONE"]
_LABELS_SET = set(LABELS)


def load_cases(cases_dir: str, labels_path: str) -> list[tuple[str, str, Path]]:
    """Return [(filename, expected_label_uppercased, path)] for labeled cases
    that exist on disk. Missing files are skipped with a warning.
    """
    labels = json.loads(Path(labels_path).read_text(encoding="utf-8"))
    cases: list[tuple[str, str, Path]] = []
    for fname, expected in labels.items():
        path = Path(cases_dir) / fname
        if not path.exists():
            print(
                f"WARN: labels 中有 {fname} 但 cases 目录未找到，跳过",
                file=sys.stderr,
            )
            continue
        cases.append((fname, str(expected).strip().upper(), path))
    return cases


async def run_one(fname: str, expected: str, path: Path) -> dict:
    """Classify one case file. Never raises — errors become a predicted 'ERROR'."""
    ext = path.suffix.lower()
    try:
        raw = path.read_bytes()
        result = await classify_file(raw, ext)
        return {
            "file": fname,
            "expected": expected,
            "predicted": predicted_label(result),
            "matched": result.matched,
            "document_id": result.document_id,
            "confidence": result.confidence,
            "reason": result.reason,
            "error": result.error,
            "errored": False,
        }
    except Exception as exc:  # noqa: BLE001 — eval must keep going case-by-case
        return {
            "file": fname,
            "expected": expected,
            "predicted": "ERROR",
            "matched": False,
            "document_id": None,
            "confidence": 0.0,
            "reason": str(exc),
            "error": str(exc),
            "errored": True,
        }


async def _run_all(cases: list[tuple[str, str, Path]]) -> list[dict]:
    results: list[dict] = []
    for fname, expected, path in cases:
        r = await run_one(fname, expected, path)
        ok = r["predicted"] == r["expected"]
        marker = "✓" if ok else "✗"
        suffix = f" [ERR: {r['error']}]" if r["errored"] else ""
        print(
            f"  {marker} {fname}: expected={r['expected']} "
            f"predicted={r['predicted']}{suffix}"
        )
        results.append(r)
    return results


def compute_metrics(results: list[dict]) -> dict:
    """Compute accuracy, per-class P/R/F1, macro-F1, confusion matrix, error count.

    Errored cases (predicted 'ERROR') count as a wrong prediction: they add a
    false negative to the true class and never add a false positive (ERROR is
    not a real class). They are also reported in a separate `errors` bucket.
    """
    tp: Counter = Counter()
    fp: Counter = Counter()
    fn: Counter = Counter()
    support: Counter = Counter()
    confusion: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    errors = 0

    for r in results:
        exp = r["expected"]
        pred = r["predicted"]
        support[exp] += 1
        confusion[exp][pred] += 1
        if r.get("errored") or pred == "ERROR":
            errors += 1
        if pred == exp:
            tp[exp] += 1
        else:
            fn[exp] += 1
            if pred in _LABELS_SET:
                fp[pred] += 1

    per_class: dict[str, dict] = {}
    for label in LABELS:
        p_denom = tp[label] + fp[label]
        r_denom = tp[label] + fn[label]
        precision = tp[label] / p_denom if p_denom else 0.0
        recall = tp[label] / r_denom if r_denom else 0.0
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
        per_class[label] = {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "support": support[label],
        }

    total = len(results)
    correct = sum(1 for r in results if r["predicted"] == r["expected"])
    accuracy = correct / total if total else 0.0
    # Macro-F1 averages over classes that actually appear (support > 0),
    # matching sklearn's default behavior for the perfect-prediction case.
    supported = [c for c in per_class.values() if c["support"] > 0]
    macro_f1 = sum(c["f1"] for c in supported) / len(supported) if supported else 0.0

    return {
        "total": total,
        "correct": correct,
        "accuracy": accuracy,
        "macro_f1": macro_f1,
        "errors": errors,
        "per_class": per_class,
        "confusion_matrix": {a: dict(b) for a, b in confusion.items()},
    }


def _print_report(metrics: dict, results: list[dict]) -> None:
    print("\n===== 评估结果 =====")
    print(
        f"总数: {metrics['total']}  正确: {metrics['correct']}  "
        f"错误(LLM): {metrics['errors']}"
    )
    print(
        f"Accuracy: {metrics['accuracy']:.4f}   Macro-F1: {metrics['macro_f1']:.4f}\n"
    )
    print(f"{'类别':<6}{'precision':>11}{'recall':>9}{'f1':>9}{'support':>9}")
    for label in LABELS:
        c = metrics["per_class"][label]
        print(
            f"{label:<6}{c['precision']:>11.4f}{c['recall']:>9.4f}"
            f"{c['f1']:>9.4f}{c['support']:>9}"
        )

    print("\n混淆矩阵 (行=实际, 列=预测):")
    cols = LABELS + (["ERROR"] if metrics["errors"] else [])
    print("实际\\预测  " + "  ".join(f"{c:>6}" for c in cols))
    for actual in LABELS:
        row = metrics["confusion_matrix"].get(actual, {})
        cells = "  ".join(f"{row.get(c, 0):>6}" for c in cols)
        print(f"{actual:>8}  {cells}")


def main() -> None:
    parser = argparse.ArgumentParser(description="评估模板分类器")
    parser.add_argument(
        "--cases", default="tests/fixtures/classifier/cases", help="案例目录"
    )
    parser.add_argument(
        "--labels", default="tests/fixtures/classifier/labels.json", help="标注文件"
    )
    parser.add_argument(
        "--report", default="reports/classifier_eval.json", help="JSON 报告输出路径"
    )
    args = parser.parse_args()

    if not settings.openai_api_key.strip():
        print(
            "ERROR: OPENAI_API_KEY 未配置，无法运行评估。请在 .env 中设置后重试。",
            file=sys.stderr,
        )
        sys.exit(2)

    cases = load_cases(args.cases, args.labels)
    if not cases:
        print(
            "没有可评估的案例（labels.json 为空或文件均不存在）。",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"评估 {len(cases)} 个案例（模型: {settings.openai_model}）...\n")
    results = asyncio.run(_run_all(cases))
    metrics = compute_metrics(results)
    _print_report(metrics, results)

    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps({"metrics": metrics, "results": results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n完整报告已写入: {args.report}")


if __name__ == "__main__":
    main()
