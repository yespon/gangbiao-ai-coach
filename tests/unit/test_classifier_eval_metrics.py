import importlib.util
import json
from pathlib import Path

import pytest


def _load_eval_module():
    """Load scripts/eval_classifier.py as a module (scripts/ is not a package)."""
    path = Path(__file__).resolve().parents[2] / "scripts" / "eval_classifier.py"
    spec = importlib.util.spec_from_file_location("eval_classifier", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_compute_metrics_perfect_predictions():
    mod = _load_eval_module()
    results = [
        {"expected": "D1", "predicted": "D1"},
        {"expected": "D5", "predicted": "D5"},
        {"expected": "NONE", "predicted": "NONE"},
    ]
    m = mod.compute_metrics(results)
    assert m["accuracy"] == 1.0
    assert m["macro_f1"] == 1.0
    assert m["per_class"]["D1"]["precision"] == 1.0
    assert m["per_class"]["D1"]["recall"] == 1.0
    assert m["per_class"]["D1"]["support"] == 1
    assert m["errors"] == 0


def test_compute_metrics_misclassification_and_error_bucket():
    mod = _load_eval_module()
    results = [
        {"expected": "D1", "predicted": "D5"},    # D1 FN, D5 FP
        {"expected": "D5", "predicted": "D5"},    # D5 TP
        {"expected": "D3", "predicted": "ERROR"},  # errored -> D3 FN, no FP, error bucket
    ]
    m = mod.compute_metrics(results)
    # accuracy: 1 of 3 correct
    assert m["accuracy"] == pytest.approx(1 / 3)
    # D1: never predicted -> precision 0 (denominator 0), recall 0
    assert m["per_class"]["D1"]["precision"] == 0.0
    assert m["per_class"]["D1"]["recall"] == 0.0
    # D5: tp=1, fp=1, fn=0 -> precision 0.5, recall 1.0
    assert m["per_class"]["D5"]["precision"] == 0.5
    assert m["per_class"]["D5"]["recall"] == 1.0
    # D3: errored counts as FN -> recall 0
    assert m["per_class"]["D3"]["recall"] == 0.0
    # confusion matrix rows=actual, cols=predicted
    assert m["confusion_matrix"]["D1"]["D5"] == 1
    assert m["confusion_matrix"]["D3"]["ERROR"] == 1
    # error bucket
    assert m["errors"] == 1


def test_compute_metrics_empty_results():
    mod = _load_eval_module()
    m = mod.compute_metrics([])
    assert m["accuracy"] == 0.0
    assert m["macro_f1"] == 0.0
    assert m["errors"] == 0
    assert m["total"] == 0


def test_load_cases_skips_missing_and_uppercases_labels(tmp_path):
    mod = _load_eval_module()
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    (cases_dir / "a.xlsx").write_bytes(b"fake")
    (cases_dir / "b.xlsx").write_bytes(b"fake")  # not in labels -> skipped
    labels = {"a.xlsx": "d1", "missing.xlsx": "D2"}  # missing file + lowercase label
    (tmp_path / "labels.json").write_text(json.dumps(labels), encoding="utf-8")

    cases = mod.load_cases(str(cases_dir), str(tmp_path / "labels.json"))

    assert [c[0] for c in cases] == ["a.xlsx"]  # missing skipped, b not labeled
    assert cases[0][1] == "D1"  # label uppercased
    assert cases[0][2] == cases_dir / "a.xlsx"
