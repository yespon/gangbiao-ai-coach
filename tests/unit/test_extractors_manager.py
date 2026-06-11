from io import BytesIO

import pytest
from openpyxl import Workbook

import app.extractors.manager as manager_module


class _DummyUploadFile:
    def __init__(self, filename: str, content_type: str, raw_bytes: bytes):
        self.filename = filename
        self.content_type = content_type
        self._raw_bytes = raw_bytes

    async def read(self) -> bytes:
        return self._raw_bytes


@pytest.mark.asyncio
async def test_save_attachments_keeps_structured_spreadsheet_preview(monkeypatch, tmp_path):
    monkeypatch.setattr(manager_module, "BASE_DIR", tmp_path)
    monkeypatch.setattr(manager_module, "UPLOAD_ROOT", tmp_path / "uploads")
    monkeypatch.setattr(manager_module, "ATTACHMENT_EXCERPT_CHARS", 1200)
    monkeypatch.setattr(manager_module, "ATTACHMENT_HINT_CHARS", 300)

    wb = Workbook()
    ws = wb.active
    ws.title = "岗标"
    ws.append(["岗位任务", "客户价值", "目的", "成果"])
    for idx in range(1, 40):
        ws.append([f"任务{idx}", f"价值{idx}", f"目的{idx}", f"成果{idx}"])

    buf = BytesIO()
    wb.save(buf)

    file_obj = _DummyUploadFile(
        filename="gb.xlsx",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        raw_bytes=buf.getvalue(),
    )

    saved_meta, hints = await manager_module._save_attachments(
        session_id="s1",
        files=[file_obj],
    )

    assert saved_meta
    excerpt = saved_meta[0]["excerpt"]
    assert len(excerpt) <= 1200
    assert "[Structured] 识别列: 岗位价值->B列" in excerpt
    assert "岗位价值\t岗位任务\t任务目的\t任务成果" in excerpt
    assert hints
    assert "可读摘要" in hints[0]


def test_extract_attachment_excerpt_spreadsheet_contains_structured_block():
    wb = Workbook()
    ws = wb.active
    ws.title = "岗标"
    ws.append(["岗位任务", "客户价值", "目的", "成果"])
    ws.append(["梳理流程", "提升满意度", "对齐标准", "按期交付"])

    buf = BytesIO()
    wb.save(buf)

    excerpt = manager_module._extract_attachment_excerpt(buf.getvalue(), "demo.xlsx")

    assert "[Structured]" in excerpt
    assert "岗位价值" in excerpt
    assert "提升满意度" in excerpt
