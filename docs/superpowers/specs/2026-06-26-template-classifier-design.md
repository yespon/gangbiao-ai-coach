# 模板分类器服务（Template Classifier）设计

- 日期：2026-06-26
- 状态：待评审
- 关联文件：`app/services/prompts.py`（`template_classifier` 提示词常量）

## 1. 背景与目标

`app/services/prompts.py` 中的 `template_classifier` 是一段用于把用户上传的 Excel 文件归入 7 份已知模板（D1~D7）的提示词。当前仅有提示词，没有可调用的服务，也无法度量其分类质量。

本设计实现一个**模板分类器服务**，并配套：

1. **确定性单元测试**（mock LLM，不依赖真实 API key，验证抽取格式、JSON 解析、端到端解析）。
2. **评估脚手架 CLI**：读取真实 Excel 案例 + 标注，调用真实 LLM 跑分类，输出准确率、每类 precision/recall/F1、macro-F1、混淆矩阵。

评估（准确率/召回/精度）在用户后续提供案例后由评估脚手架完成，本次不交付真实案例。

## 2. 范围

### 2.1 本次交付（In scope）

- 新增 `app/services/template_classifier.py`：专用 Excel 抽取 + LLM 调用 + JSON 解析 + 结果模型。
- 新增 `tests/unit/test_template_classifier.py`：确定性单元测试。
- 新增 `scripts/eval_classifier.py`：评估 CLI。
- 新增空目录 `tests/fixtures/classifier/cases/` 与模板 `tests/fixtures/classifier/labels.json`，供用户后续投放案例。

### 2.2 不在本次范围（Out of scope）

- 不新增 HTTP 路由（仅服务模块；后续如需接入应用再加路由）。
- 不支持 CSV（仅 Excel：`.xlsx` / `.xls`，与案例形式一致）。
- 不做共享 LLM 客户端重构（分类器与聊天服务的调用需求不同：一次性 JSON vs 流式文本；温度 0 vs 0.2）。分类器保持自包含、易于隔离测试。

## 3. 模块布局

```
app/services/template_classifier.py          # 服务（新）
tests/unit/test_template_classifier.py       # 确定性单元测试（新）
scripts/eval_classifier.py                   # 真实 LLM 评估 CLI（新）
tests/fixtures/classifier/cases/             # 用户后续投放 .xlsx/.xls
tests/fixtures/classifier/labels.json        # { "<filename>": "D1".."D7" | "NONE" }
```

## 4. 组件设计

### 4.1 结果模型 `ClassificationResult`（pydantic）

字段与提示词输出 schema 对齐：

| 字段 | 类型 | 说明 |
|------|------|------|
| `matched` | `bool` | 是否属于 7 份已知文档之一 |
| `document_id` | `str \| None` | `D1`~`D7`；未匹配为 `None` |
| `version` | `str \| None` | `多等级版` / `普通版` / `通用版` / `None` |
| `stage` | `str \| None` | 阶段名称全称；未匹配为 `None` |
| `confidence` | `float` | 0.0~1.0 |
| `matched_signals` | `list[str]` | 触发匹配的具体文字证据 |
| `reason` | `str` | 一句话说明 |
| `error` | `str \| None = None` | 内部字段：解析/调用失败原因（默认 `None`） |

校验：`document_id` 若非 `None` 必须落在 `{D1..D7}`；否则强制为 `None`。`confidence` clamp 到 `[0,1]`。

### 4.2 专用 Excel 抽取 — `extract_cells_for_classification(raw_bytes, ext) -> str`

按提示词“输入格式”逐字产出：

```
文件尺寸：{rows}行 × {cols}列
单元格内容（[行,列] 内容）：
[1,1] 岗位名称
[1,2] 服务客户
...
```

实现要点：

- 读取库与 `app/extractors/spreadsheet.py` 一致：`.xlsx` 用 `openpyxl.load_workbook`，`.xls` 用 `xlrd.open_workbook`。
- **仅第一个非空工作表**（每份文件对应一份模板）。
- 仅输出非空单元格；`value.strip()` 仅去首尾空白——**保留内部空格**，确保合并单元格指纹（如 `拟认证等级              实际时间投入占比`）原样存活。
- 浮点整数值 → 整数字符串（与 `_normalize_cell_value` 一致）。
- 合并单元格：只读左上角值（openpyxl 其余位置自然为 `None`），**不填充**，避免指纹文字被重复。
- **不做岗标标签改写**（`目的:` → `任务目的：` 等），**不注入** `[Sheet]/[Structured]/[Raw]` 标记——这些会篡改 D1/D5 的关键指纹（`岗位任务的目的` vs `岗位任务的目的和成果`）。

`rows`/`cols`：该工作表的实际尺寸（openpyxl 的 `max_row`/`max_column`，xlrd 的 `nrows`/`ncols`）。

### 4.3 LLM 调用 — `_call_llm_json(messages) -> str`

- httpx `AsyncClient` POST 到 `{settings.openai_base_url}/chat/completions`，`temperature: 0`，`Authorization: Bearer {settings.openai_api_key}`，与 `llm_service._call_llm` 同模式。
- 非 200 → `raise RuntimeError(f"LLM 调用失败: {status} {body}")`。
- 返回 `data["choices"][0]["message"]["content"]` 原始文本。

### 4.4 分类入口

- `async classify_text(text) -> ClassificationResult`：构造 `[{system: template_classifier}, {user: text}]`，调用 `_call_llm_json`，解析。
- `async classify_file(raw_bytes, ext) -> ClassificationResult`：`ext = extract → classify_text`。

### 4.5 JSON 解析（容错，永不崩溃管线）

`parse_classification(raw_text) -> ClassificationResult`：

1. 去除 ```json 代码块围栏。
2. 若整体非纯 JSON，用正则抽取第一个 `{...}` 块。
3. `json.loads`；失败 → 返回 `ClassificationResult(matched=False, document_id=None, reason="解析失败: <detail>", error=<detail>)`。
4. 校验/归一：`document_id` ∈ `{D1..D7, None}`（其余强制 `None`）；`matched` 转 `bool`；`confidence` 转 `float` 并 clamp；其余字段按 schema 归一。
5. 解析失败也是一种“预测”：`matched=False` → 评估时记为 `NONE`。

### 4.6 API key 缺失处理

- **缺失 API key → 服务 `raise RuntimeError`**（与聊天的优雅回退不同）。
  - 理由：分类是内部管线而非用户可见对话；静默回退会污染评估指标，且单元测试已通过 monkeypatch 隔离真实调用。
- 评估 CLI 在跑任何 case 前预先校验 key，缺失即清晰报错中止。

## 5. 确定性单元测试（`tests/unit/test_template_classifier.py`）

全部在 `OPENAI_API_KEY=""` 下运行（沿用 `conftest.py` 的 `isolate_runtime_state`），无真实调用。

1. **抽取测试**：openpyxl 内存建表，含一个合并单元格 + 一个浮点整数值单元格 → 断言：
   - 输出含 `文件尺寸：N行 × M列` 头；
   - 非空单元格以 `[行,列] 内容` 列出；
   - 合并单元格只出现左上角值（无重复）；
   - 内部多空格原样保留；
   - 浮点整数输出为整数字符串。
2. **JSON 解析测试**：分别喂入 (a) 干净 JSON、(b) ```json 围栏、(c) JSON + 尾部散文、(d) 畸形文本 → 断言各自的 `ClassificationResult` 正确（成功解析 / `matched=False` + `error`）。
3. **端到端 `classify_text`**：monkeypatch `_call_llm_json` 返回固定 JSON → 断言发送的 messages 正确（system 为 `template_classifier`，user 为输入文本）、返回的 `ClassificationResult` 字段正确。
4. **`classify_file`**：monkeypatch `_call_llm_json`，喂内存 xlsx → 断言抽取文本被正确拼入 user 消息。

## 6. 评估 CLI（`scripts/eval_classifier.py`）

```
python scripts/eval_classifier.py \
  --cases tests/fixtures/classifier/cases \
  --labels tests/fixtures/classifier/labels.json \
  --report reports/classifier_eval.json
```

行为：

- 启动前校验 `settings.openai_api_key` 非空，否则报错中止。
- 加载 `labels.json`（`{filename: D1..D7 | NONE}`）；扫描 `--cases` 目录下的 `.xlsx`/`.xls`，仅评估 `labels.json` 中登记的文件。
- 逐案例：抽取 → `classify_file`（真实 LLM）→ 预测标签（`matched=False` → `NONE`）。**捕获每案例的原始 LLM 文本与 `ClassificationResult`**，便于排查误分类。
- **LLM 调用层出错**（非 200 / 超时 / 网络异常 / 不支持扩展名，服务 `raise RuntimeError`）：该案例记 ERROR（计入错误、判错），并在报告中单独列出错误桶——避免抖动的 key/网络抬高准确率。
- **JSON 不可解析**（服务返回 `matched=False` + `error`）：预测为 `NONE`，按**正常预测**处理（若真实标签非 NONE 则自然判错），不计入错误桶。
- 指标：
  - **accuracy**；
  - **每类 precision / recall / F1**（类别 = `D1..D7, NONE`）；
  - **macro-F1**；
  - **混淆矩阵**（行=实际，列=预测，标签顺序 `D1..D7, NONE`）。
- 输出：stdout 人类可读表格 + 完整 JSON 报告（每案例预测 + 指标）写入 `--report`，便于跨 run 对比。

## 7. 错误处理汇总

| 情况 | 服务行为 | 评估 CLI 行为 |
|------|----------|----------------|
| 缺失 API key | `raise RuntimeError` | 启动前报错中止 |
| LLM 非 200 | `raise RuntimeError(status+body)` | 该案例记 ERROR（计入错误，判错） |
| JSON 不可解析 | 返回 `matched=False` + `error` | 预测 `NONE`（按预测处理） |
| 不支持的扩展名 | `raise RuntimeError` | 该案例记 ERROR |

## 8. 与现有代码的关系

- 复用 `app.core.config.settings`（openai_api_key/model/base_url）。
- 复用 openpyxl/xlrd 读取模式（参考 `app/extractors/spreadsheet.py`，但**不**复用其输出，避免标签改写污染指纹）。
- 不修改 `llm_service.py`、`spreadsheet.py` 及任何现有路由。
