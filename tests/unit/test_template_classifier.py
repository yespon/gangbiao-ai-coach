import pytest

from app.services import template_classifier as svc
from app.services.template_classifier import ClassificationResult, parse_classification


def test_parse_clean_json():
    raw = (
        '{"matched": true, "document_id": "D1", "version": "多等级版", '
        '"stage": "阶段一【岗位价值和岗位任务】", "confidence": 0.97, '
        '"matched_signals": ["列头含「岗位任职资格等级」"], "reason": "命中D1"}'
    )
    r = parse_classification(raw)
    assert r.matched is True
    assert r.document_id == "D1"
    assert r.version == "多等级版"
    assert r.confidence == 0.97
    assert r.matched_signals == ["列头含「岗位任职资格等级」"]
    assert r.error is None


def test_parse_json_inside_code_fence_with_prose():
    raw = (
        "好的，分类结果如下：\n"
        "```json\n"
        '{"matched": true, "document_id": "D5", "confidence": 0.9, '
        '"matched_signals": [], "reason": "x"}\n'
        "```\n"
        "以上。"
    )
    r = parse_classification(raw)
    assert r.document_id == "D5"
    assert r.matched is True


def test_parse_json_with_trailing_prose_no_fence():
    raw = (
        '{"matched": false, "document_id": null, "confidence": 0.0, '
        '"matched_signals": [], "reason": "无匹配"} 这是一段说明文字'
    )
    r = parse_classification(raw)
    assert r.matched is False
    assert r.document_id is None
    assert r.error is None


def test_parse_malformed_returns_error_result():
    r = parse_classification("这不是JSON")
    assert r.matched is False
    assert r.document_id is None
    assert r.error is not None
    assert "解析失败" in r.reason


def test_coerce_invalid_document_id_becomes_none_and_unmatched():
    r = parse_classification('{"matched": true, "document_id": "D9", "confidence": 1.5}')
    assert r.document_id is None
    assert r.matched is False  # invalid id forces unmatched
    assert r.confidence == 1.0  # clamped to [0, 1]


def test_coerce_lowercase_document_id_uppercased():
    r = parse_classification('{"matched": true, "document_id": "d1", "confidence": -0.2}')
    assert r.document_id == "D1"
    assert r.confidence == 0.0  # clamped


def test_classification_result_defaults():
    r = ClassificationResult()
    assert r.matched is False
    assert r.document_id is None
    assert r.confidence == 0.0
    assert r.matched_signals == []
    assert r.error is None
