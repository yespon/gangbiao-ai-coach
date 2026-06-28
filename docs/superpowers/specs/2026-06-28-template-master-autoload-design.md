# 模板母版自动加载 — 设计文档

> 日期：2026-06-28
> 分支：基于 `feature/template-classifier` 新建分支实现
> 状态：设计待审

## 1. 背景与目标

项目已有 `template_classifier` 服务，能把上传的 Excel 文件分类为 7 份已知模板之一（D1..D7）。目前该分类器仅作为库 + 评估脚本存在，未接入任何实时路由。

聊天系统当前的行为：
- 会话创建时固定加载 `岗位标准化母体.history.json`（OpenAI chat history 格式，含 system/user/assistant 多消息）作为默认上下文。
- `_build_model_messages`（`llm_service.py`）硬编码一行 system 提示词 `"你是岗位标准化 AI 教练…"`，再拼历史对话与当前用户消息。
- 母体本质上是 D5（岗标卡一·普通版）对应的那份"母版"。

**目标：**
1. 用户上传 Excel 附件时，自动识别其模板类型（D1..D7）。
2. 根据模板类型自动加载对应的"母版提示词"（母版即母体，统一为 OpenAI chat history 格式）。
3. 模板变多，用专用目录存放各模板母版。

**非目标：**
- 不修改 `template_classifier` 的分类逻辑本身。
- 不在本期提供 8 份母版的实际文本内容（由用户后续提供）；本设计只定义加载机制与目录结构。

## 2. 关键决策（已与用户确认）

| 决策点 | 选择 |
|---|---|
| 母版来源 | 用户将提供 8 份母版（D1..D7 + 1 份通用） |
| 母版格式 | 与 `岗位标准化母体.history.json` 相同的 OpenAI chat history JSON（含 system/user/assistant），加载方式不变 |
| 母版与母体关系 | "母体"即母版；现有母体 = D5 母版；8 份统称母版 |
| 切换时机 | 消息级：每次带附件消息按最新附件重新分类、重新加载母版 |
| 无附件 | 加载通用母版（`_generic`） |
| 拒识（非标准模板/识别失败） | 拦截，报错让用户重传 |
| 多附件模板不一致 | 拦截，提示用户选（不调主 LLM） |
| 分类开销 | 每次带附件都调分类器，不缓存 |
| 支持的附件 | 当前仅支持 Excel（.xlsx/.xls）；非 Excel 视为非标准模板，拦截 |
| 无附件时的通用母版 | 额外一份（区别于 D5 默认） |

## 3. 架构与组件

### 3.1 母版目录（新增）

```
master/                               # 8 份母版（用户后续提供文本）
├── _generic.history.json             # 通用母版（无附件/兜底）
├── D1.history.json
├── D2.history.json
├── D3.history.json
├── D4.history.json
├── D5.history.json                   # 内容 = 现在的 岗位标准化母体.history.json
├── D6.history.json
└── D7.history.json
```

- 每份文件均为 OpenAI chat history 格式：`{"version","format","generated_at","messages":[{role,content,metadata}],"source_file"}`。
- 母版内部已包含 system 提示词；`_build_model_messages` 不再硬编码 system 行。

### 3.2 组件

**新增 `app/services/template_prompt_service.py`：**
- 母版注册表：`{document_id: Path}`，`document_id` ∈ `{"D1".."D7", None}`，`None` 映射 `_generic`。启动时扫描 `master/` 一次性加载路径。
- `Resolution` dataclass：
  - `status: "ok" | "intercept"`
  - `master_path: Path | None`（ok 时指向应加载的母版文件）
  - `document_id: str | None`
  - `intercept_message: str`（intercept 时给用户的中文提示）
  - `reason: str`（内部原因，记日志用）
- `async resolve_master(attachments, file_root) -> Resolution`：核心编排。
- `validate_master_registry(logger)`：启动校验，8 份文件应齐全，缺失记 ERROR 不抛。

**改 `app/services/context_service.py`：**
- 新增 `load_master_messages(master_path: Path, logger) -> list[ChatMessage]`：与现有 `load_default_context_messages` 逻辑相同（解析 chat history JSON → `ChatMessage(is_context=True)`），只是路径参数化。
- 现有 `load_default_context_messages(context_file, logger)` 保留为薄包装（向后兼容现有调用点），或直接迁移调用点。

**改 `app/services/llm_service.py`：**
- `_build_model_messages(session, user_msg, master_messages=None)`：
  - `master_messages` 非 None 时，作为 messages 前缀（克隆、`is_context=True`），替代原硬编码的 system 行。
  - `master_messages` 为 None 时回退原行为（硬编码 system 行）——保持向后兼容。

**改 `app/api/v1/routes/chat.py`（`/chat` 与 `/chat/stream`）：**
- 在 `_append_user_message_with_attachments` 之后、`_build_model_messages` 之前插入：
  1. `resolution = await resolve_master(user_msg.attachments, UPLOAD_ROOT)`
  2. 若 `intercept` → `raise HTTPException(400, resolution.intercept_message)`
  3. `master_messages = load_master_messages(resolution.master_path, LOGGER)`
  4. 传给 `_build_model_messages(..., master_messages)`

**改 `app/services/session_service.py` / `app/api/v1/routes/sessions.py`：**
- 会话创建时不再固定加载 `CONTEXT_FILE`（母体）。创建空会话；母版改由每轮发消息时按附件动态加载。
- `CONTEXT_FILE` 常量保留（指向 `master/D5.history.json` 或保留旧路径作迁移参考），但会话创建路径不再调用 `load_default_context_messages`。

### 3.3 关键变化

- 母版从"会话创建时固定加载一份"变为"每轮按附件动态选一份并加载"。
- `_build_model_messages` 的 system 行来源从硬编码改为母版前缀。
- 现有 `岗位标准化母体.history.json` 迁移到 `master/D5.history.json`。

## 4. 数据流

### 4.1 主流程：带附件发消息（识别成功）

```
1. POST /chat  (session_id, message, files=[.xlsx])
2. chat.py: user_msg = _append_user_message_with_attachments(...)
   → 保存附件、提取 excerpt、组装 user_msg.content（同现有）
3. chat.py: resolution = await resolve_master(user_msg.attachments, UPLOAD_ROOT)
4. template_prompt_service.resolve_master:
   a. 无附件 → ok, _generic（本主流程不触发，见 4.2）
   b. 含非 Excel 附件 → intercept, "请上传岗位标准化模板 Excel 文件（.xlsx/.xls）"
   c. 逐份读字节 → classify_file(ext)
      - 任一 matched=False 或 errored → intercept, "附件未识别为标准模板，请确认后重传"
   d. 收集 document_id 集合：
      - |{ids}| > 1 → intercept, "检测到多份不同模板附件，请说明你想用哪份进行辅导"
      - |{ids}| == 1 → ok, master=D{id}
5. chat.py: intercept → raise HTTPException(400, intercept_message)
6. chat.py: master_messages = load_master_messages(resolution.master_path, LOGGER)
7. chat.py: llm_messages = _build_model_messages(session, user_msg, master_messages)
8. llm_service:
   - messages = master_messages(克隆, is_context=True) + 历史对话 + 当前 user_msg
   - 不再加原硬编码 system 行
9. _call_llm / _call_llm_stream → 回复
```

### 4.2 分支：无附件

- 第 3 步 `resolve_master` 直接返回 `ok, _generic`，加载通用母版，其余同主流程。

### 4.3 分支：拦截（4 种触发）

| 触发 | HTTP | intercept_message |
|---|---|---|
| 含非 Excel 附件 | 400 | 请上传岗位标准化模板 Excel 文件（.xlsx/.xls） |
| 任一附件识别失败/非标准 | 400 | 附件未识别为标准模板，请确认后重传 |
| 多附件模板不一致 | 400 | 检测到多份不同模板附件，请说明你想用哪份进行辅导 |
| 对应母版文件缺失 | 400 | 模板母版尚未配置，请联系管理员 |

### 4.4 边界

- **每轮重新解析**：不复用上一轮母版；每条带附件消息重新分类（消息级）。
- **母版作为 context 前缀**：母版消息 `is_context=True`，每轮重新构建 messages，母版只在前缀出现一次，不累积。
- **拦截时用户消息已入库**：`_append_user_message_with_attachments` 先于 `resolve_master` 执行，故拦截时用户消息已存入会话历史与 DB（用户可接受，便于其重发后上下文连续）。
- **拦截在主 LLM 之前**：拦截不消耗主模型调用，但已消耗分类器调用（不可逆，可接受）。

## 5. 错误处理

| 场景 | 处理 |
|---|---|
| 分类器 LLM 调用失败（网络/超时/4xx） | `classify_file` 抛 `RuntimeError` → 拦截，"附件未识别为标准模板，请确认后重传"。不抛 500，降级为用户可重试。 |
| 母版文件缺失 | 启动校验记 ERROR；请求时若解析到该 id → 拦截"模板母版尚未配置，请联系管理员"。 |
| 母版 JSON 解析失败 | `load_master_messages` 返回 `[]` 时视为硬故障。因加载发生在 `resolve_master` 之后（数据流第6步），由 `chat.py` 在加载后检查 `master_messages` 为空 → 拦截 `HTTPException(400, "模板母版加载失败，请联系管理员")` + ERROR 日志，避免静默无母版。 |
| `OPENAI_API_KEY` 未配置 | 分类器自身抛错 → 走"识别失败"拦截。 |
| 附件字节读取失败 | 拦截，"附件读取失败，请重传"。 |
| 同一文件多份（相同 document_id） | 不算冲突（`|{ids}|==1`），正常加载。 |

**启动校验**：应用启动时扫描 `master/`，8 份文件应齐全；缺失记 ERROR 但不阻止启动（便于增量补齐母版）。

## 6. 测试

沿用现有 TDD 风格（`monkeypatch` + `pytest.mark.asyncio`），不实调 LLM（mock `classify_file`）。

### 6.1 新增 `tests/unit/test_template_prompt_service.py`

- `resolve_master` 无附件 → `ok, _generic`
- 单 Excel 识别为 D5 → `ok, D5`
- 含非 Excel 附件 → `intercept`
- 单附件识别失败（`matched=False`）→ `intercept`
- 分类器抛错 → `intercept`
- 多附件不同模板 → `intercept`
- 多附件同模板 → `ok`
- 对应母版文件缺失 → `intercept`
- 启动注册表：`master/` 缺文件 → 日志 ERROR，不抛

### 6.2 新增 `tests/integration/test_chat_template_master.py`

- 上传 D5 xlsx（mock `classify_file` 返回 D5）→ 回复用 D5 母版前缀（断言 messages[0] 来自母版、无原硬编码 system 行）
- 上传非标准 xlsx → HTTP 400 + 提示语
- 上传 .pdf → HTTP 400
- 无附件发消息 → 用 `_generic` 母版

### 6.3 改现有 chat 测试

凡依赖原硬编码 system 行或会话创建预加载母体的断言，改为母版动态加载行为。需先排查受影响测试：`test_api_basics`、`test_chat_stream_done_payload`、`test_llm_fallback_behavior` 等构建 messages 或断言 system 行的用例。

## 7. 实现顺序（供 writing-plans 细化）

1. 建 `master/` 目录与占位文件（8 份，内容待用户提供；先用现有母体填 D5）
2. `template_prompt_service.py`：注册表 + `Resolution` + `resolve_master` + 启动校验
3. `context_service.load_master_messages` + 迁移 `load_default_context_messages` 调用点
4. `llm_service._build_model_messages` 增 `master_messages` 参数
5. `chat.py` 两个路由接入 `resolve_master` + 拦截 + 加载
6. `session_service`/`sessions.py` 去掉会话创建预加载母体
7. 单测 + 集成测试，改受影响现有测试
8. 迁移 `岗位标准化母体.history.json` → `master/D5.history.json`

## 8. 配置变更

- 新增 `MASTER_DIR = BASE_DIR / "master"`（`app/core/config.py`）。
- `CONTEXT_FILE` 语义变更：从"会话默认上下文"退化为"D5 母版路径"，或直接由 `MASTER_DIR / "D5.history.json"` 替代。
