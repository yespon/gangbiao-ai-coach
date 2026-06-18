# 管理后台迭代优化设计

> 日期：2026-06-18
> 范围：管理后台 `/admin` 用户管理（分页 + 多维过滤）、对话历史 AI 速览、意见反馈（聊天页 + admin 反馈模块）
> 前置设计：`2026-06-16-admin-panel-user-management-design.md`（用户管理与对话历史）、`2026-06-14-admin-sso-whitelist-design.md`（SSO 白名单）

## 1. 背景与目标

`/admin` 后台已上线用户管理与对话历史两个模块。本次迭代基于线上反馈，做三块增强：

1. **用户管理** 数据量增长后缺乏分页与精细过滤能力，管理员定位人员成本高。
2. **对话历史** 缺一个 AI 速览能力，管理员/教练要快速了解某个会话的主题与辅导走向时只能逐条翻阅。
3. **意见反馈** 缺一个统一的反馈入口；当前管理员无法系统收集一线用户对系统的建议与问题。

三块都只服务于 `/admin` 后台扩展，不改变已有数据模型的主外键关系（`users`、`managed_users`、`chat_sessions`、`chat_messages` 不变）。

## 2. 已确认需求

### 2.1 用户管理

1. 用户列表必须支持分页，**默认每页 30 条**，可由用户配置为 10/30/50/100。
2. 页大小选择**仅本地保存**（`localStorage`），不上服务器，不占用户档案字段。
3. 过滤能力（任选叠加，AND 关系）：
   - 关键词模糊搜索：工号 / 姓名 / 邮箱 / 一级部门
   - 主角色：全部 / 管理员 / 教练 / 学员
   - 启用状态：全部 / 已启用 / 已禁用
   - 按所属教练过滤（含「未分配」选项）
   - 一级部门精确匹配
   - 邮箱是否存在：有邮箱 / 无邮箱 / 全部
4. 必须有「重置」按钮清空全部过滤。
5. 分页器显示 `共 N 条 / 第 X / Y 页` 与页大小下拉。
6. 后端不存储分页偏好。

### 2.2 对话历史 AI 速览

1. 在「查看特定用户页面」会话详情 dialog 的头部右侧增加「**AI 速览**」按钮（与图 3 样式一致：胶囊浅灰描边 + 魔法棒图标），点击后展开速览。
2. 速览结果**纯文本**，非流式一次性生成，长度不超过 300 字。
3. 速览结果以**详情面板上下分**形式呈现：上方为速览区，下方为原消息列表。关闭 dialog 之前速览常驻。
4. 超长会话（消息数 > 30）由**后端截断**：保留首 5 条 + 末 25 条，并在系统消息中说明「会话共 N 条，本次仅采样首 5 与末 25」。
5. 速览按钮权限与查看该会话的权限完全一致：管理员全部可见；教练仅自己负责的学员。
6. 仅针对「已选中的某个会话」生成速览；不批处理多个会话。

### 2.3 意见反馈

1. 入口在**主聊天页**侧栏底部 popover（与图 4 一致，由「⋮」三点按钮触发），popover 菜单新增「意见反馈」一项。
2. **对所有登录用户可见**（不只是 admin）；普通用户只能提交，**查看权限仅 admin**。
3. 反馈组成：文本（必填）+ 图片（可选）。
   - 文本：1 - 1000 字符
   - 图片：最多 5 张，单张 ≤ 3MB，仅允许 `png/jpg/jpeg/webp`
4. 提交后**不主动通知** admin（无 toast / 角标 / 邮件）；admin 主动进入反馈模块查看。
5. admin 后台新增**意见反馈模块**（独立页面 + 顶部导航 + 概览卡片）：
   - 列表分页（沿用用户管理那套 10/30/50/100 + localStorage 偏好）
   - 列表过滤：状态 tab（未读 / 已读 / 已处理 / 全部）+ 关键词搜索（按 content 模糊）
   - 状态自动流转：open → read（admin 首次查看详情时自动标记） → resolved（admin 手动标记）
   - 详情页显示提交人、时间、IP、UA、完整内容、附件缩略图（点击放大）、状态切换按钮
   - **不**做 markdown 渲染，纯文本展示（保留换行）
6. 图片存储在独立子目录 `UPLOAD_ROOT/feedback/<feedback_id>/`，与 chat 上传分离，便于备份与清理。

## 3. 数据模型

仅阶段 3（意见反馈）新增表。阶段 1 / 阶段 2 不改数据模型。

### 3.1 `feedback_submissions`

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | UUID | PK | |
| `user_id` | UUID | FK `users.id` ON DELETE CASCADE, NOT NULL, INDEX | 提交人 |
| `content` | TEXT | NOT NULL, CHECK 1..1000 | 应用层二次校验 |
| `status` | VARCHAR(20) | NOT NULL, DEFAULT `'open'`, INDEX | `open` / `read` / `resolved` |
| `user_agent` | VARCHAR(255) | NULL | 便于排查浏览器兼容性 |
| `ip` | VARCHAR(64) | NULL | 仅展示用，不做限流 |
| `created_at` | timestamptz | NOT NULL | |
| `read_at` | timestamptz | NULL | 首次被 admin 读取时写入 |
| `resolved_at` | timestamptz | NULL | admin 标记为已处理时写入 |

### 3.2 `feedback_attachments`

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | UUID | PK | |
| `feedback_id` | UUID | FK `feedback_submissions.id` ON DELETE CASCADE, NOT NULL, INDEX | |
| `filename` | VARCHAR(255) | NOT NULL | 原文件名（sanitized） |
| `content_type` | VARCHAR(64) | NOT NULL | |
| `size` | INTEGER | NOT NULL | 字节 |
| `saved_path` | TEXT | NOT NULL | 相对 `BASE_DIR` |
| `position` | SMALLINT | NOT NULL, CHECK 0..4 | 保持提交顺序 |
|  | | UNIQUE(`feedback_id`, `position`) | |

## 4. API 变更

### 4.1 阶段 1：用户管理

**`GET /api/v1/admin/users`**

查询参数（新增 / 扩展）：

| 名称 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `page` | int | 1 | 页码，从 1 起 |
| `page_size` | int | 30 | 1..100，超界自动夹紧 |
| `q` | string | None | 模糊匹配工号/姓名/邮箱/一级部门 |
| `role` | string | None | `admin` / `coach` / `student` |
| `enabled` | bool | None | |
| `coach_id` | UUID | None | 学员所属教练；特殊值 `__none__` 表示未分配 |
| `department_level1` | string | None | 精确匹配 |
| `has_email` | bool | None | true=邮箱非空, false=邮箱为空 |

响应：
```json
{
  "items": [ManagedUser, ...],
  "page": 1,
  "page_size": 30,
  "total": 234
}
```

`ManagedUser` 单条结构不变。`total` 通过 `select(func.count()).select_from(<filtered subquery>)` 取得，与 items 在同一 DB session 内执行（避免不可重复读差异，可接受小窗口不一致）。

排序：`ORDER BY updated_at DESC, employee_no ASC` —— 已有的 `updated_at` 排序列为稳定排序键。

### 4.2 阶段 2：AI 速览

**`POST /api/v1/admin/conversations/sessions/{session_id}/summary`**

权限：与 `GET /api/v1/admin/conversations/sessions/{id}` 完全一致（管理员全部；教练限其负责学员）。

请求：空 body。

响应：
```json
{
  "summary": "学员询问了 X 主题...",
  "sampled_count": 30,
  "total_count": 87
}
```

错误：
- 404 会话不存在
- 403 教练无权查看该会话
- 502 LLM 调用失败（带原始错误码）

### 4.3 阶段 3：意见反馈

**`POST /api/v1/feedback`** — 任意登录用户（`get_current_user`）

multipart/form-data：
- `content` (string, 必填, 1..1000)
- `images` (file × 0..5)

校验：每张图片 ≤ 3MB；扩展名 ∈ {`png`, `jpg`, `jpeg`, `webp`}；`content_type` 二次校验 `image/*`。
失败：413 / 422 + 中文错误信息。

响应：`{ id, created_at }`

**`GET /api/v1/admin/feedback`** — `require_admin`

查询参数：
- `page` (default 1), `page_size` (default 30, max 100)
- `status` (`open` / `read` / `resolved` / `all`, default `all`)
- `q` (按 content 模糊)

响应：与阶段 1 同构：
```json
{
  "items": [{
    "id": "...",
    "submitter": {"employee_no": "...", "name": "...", "email": "..."},
    "content_excerpt": "前 30 字...",
    "attachment_count": 2,
    "status": "open",
    "created_at": "..."
  }, ...],
  "total": 42,
  "page": 1,
  "page_size": 30
}
```

**`GET /api/v1/admin/feedback/{id}`** — `require_admin`

首次读取时将该条 status 由 `open` 改为 `read` 并写 `read_at`（在同一事务中完成）。

响应：
```json
{
  "id": "...",
  "submitter": {"employee_no", "name", "email", "department_level1", "primary_role"},
  "content": "完整文本",
  "status": "read",
  "user_agent": "...",
  "ip": "...",
  "created_at": "...",
  "read_at": "...",
  "resolved_at": null,
  "attachments": [
    {"id", "filename", "content_type", "size", "url"}
  ]
}
```

**`PATCH /api/v1/admin/feedback/{id}`** — `require_admin`

body：`{ "status": "read" | "resolved" }`
- 设为 `resolved` 时写 `resolved_at`（仅当当前不是 `resolved`）
- 设为 `read` 时写 `read_at`（仅当当前是 `open`）

**`GET /uploads/feedback/<feedback_id>/<position>_<uuid>.<ext>`** — 静态文件访问

复用现有静态文件服务；路由层在 `saved_path` 取出后做 prefix 校验：必须以 `feedback/` 开头，避免越权访问其他上传目录。

## 5. 后端实现要点

### 5.1 分页响应封装（阶段 1）

`app/api/v1/_pagination.py`（新增）：

```python
@dataclass
class Page:
    items: list[Any]
    page: int
    page_size: int
    total: int

def clamp_page_size(value: int | None, default: int = 30, maximum: int = 100) -> int: ...
def clamp_page(value: int | None) -> int: ...
```

`list_managed_users` 拆为 `_filtered_managed_user_stmt(filters)` + 调用处自行 `LIMIT/OFFSET` 与 `count` 查询。

### 5.2 AI 速览服务（阶段 2）

新文件 `app/services/conversation_summary_service.py`：

```python
async def summarize_conversation(
    db: AsyncSession,
    session_id: str,
    max_sampled: int = 30,
    head_keep: int = 5,
    tail_keep: int = 25,
) -> ConversationSummary:
    ...
```

`_call_llm` 使用系统消息 + 用户消息列表（每条：`[time] role: content`）。**不**走 `_call_llm_stream`。

错误处理：捕获 `httpx.HTTPStatusError`、`openai.APIError` 等，统一转 `HTTPException(502, "AI 速览生成失败")`。

### 5.3 反馈服务（阶段 3）

新文件 `app/services/feedback_service.py`：

- `create_feedback(db, user, content, files) -> FeedbackSubmission`：
  1. 启动事务
  2. 插入 `feedback_submissions`
  3. 对每张 `UploadFile`：`raw = await file.read()`，校验 `len(raw) <= 3*1024*1024`，校验扩展名与 content_type
  4. 写入 `UPLOAD_ROOT/feedback/<id>/<position>_<uuid>.<ext>`
  5. 插入 `feedback_attachments`（保证 `position` 0..4 连续）
  6. 提交

- `list_feedback(db, page, page_size, status, q) -> Page`
- `get_feedback(db, id) -> FeedbackSubmission`：含 attachments 自动 join（`selectinload`）
- `mark_status(db, id, status)`：在事务内根据当前 status 写对应时间戳

### 5.4 迁移

`alembic/versions/<rev>_add_feedback_tables.py`：

- 创建 `feedback_submissions`（含 `user_id` 外键、`status` 与 `created_at` 索引）
- 创建 `feedback_attachments`（含 `feedback_id` 外键与唯一约束）
- 不改任何已有表

## 6. 前端实现要点

### 6.1 用户管理工具栏 + 分页器（阶段 1）

`frontend/app/admin/users/page.tsx`：

- `useState` 持有 `filters`, `page`, `pageSize`
- `useEffect` 监听以上三个，触发 `listManagedUsers({...filters, page, pageSize})`
- 工具栏布局：单行 flex，wrap；按钮组（角色 / 启用状态 / 邮箱 / 教练归属）作为 `<select>`；关键词与一级部门作为 `<input>`；右侧「重置」按钮
- 分页器布局：底部 `admin-card` 内、与表格分离；左 `共 N 条 / 第 X / Y 页`、中页码跳转输入框（受控，仅在失焦时提交）、右页大小下拉
- `pageSize` 初始化：从 `localStorage["admin.users.pageSize"]` 读取，校验 ∈ {10, 30, 50, 100}，默认 30
- 切换 `pageSize` 时同步写回 `localStorage` 并把 `page` 重置为 1
- 切换任意 `filters` 时把 `page` 重置为 1

`frontend/lib/admin.ts`：
- `listManagedUsers(filters?, page?, pageSize?)` 返回 `Paginated<ManagedUser>`
- 新增类型 `Paginated<T>`, `ManagedUserFilters` (q / role / enabled / coach_id / department_level1 / has_email / coach_filter `'all' | 'unassigned' | <uuid>`)

`frontend/types/admin.ts`：
- 新增 `Paginated<T>`, `ManagedUserFilters`

### 6.2 AI 速览（阶段 2）

`frontend/app/admin/conversations/page.tsx`：

- 头部 dialog-head 区域，在 `<h3>` 与 `<button className="admin-dialog-close">` 之间插入：
  ```jsx
  <button className="admin-summary-btn" type="button"
          disabled={!selectedSession || summaryLoading}
          onClick={() => void runSummary()}>
    ✨ AI 速览
  </button>
  ```
- 详情面板结构改为：
  ```jsx
  <div className="admin-conversation-pane">
    <ConversationSummaryPanel ... />
    <div className="admin-message-list">...</div>
  </div>
  ```
- `ConversationSummaryPanel` 组件：根据 `summary` 状态显示占位 / loading / 文本 + 「重新生成」/「收起」按钮
- 切换 `selectedSession` 时清空 `summary`
- 收起 / 重新展开时不需要重新生成（保留状态）

`frontend/lib/admin.ts`：新增
- `summarizeConversation(sessionId: string): Promise<ConversationSummary>`

`frontend/types/admin.ts`：新增
- `ConversationSummary { summary, sampled_count, total_count }`

### 6.3 意见反馈（阶段 3）

#### 6.3.1 聊天页 popover

`frontend/app/page.tsx`：

- 在 popover 内、「管理后台」之后、「退出登录」之前插入：
  ```jsx
  <button type="button" onClick={() => { setShowUserMenu(false); setShowFeedbackDialog(true); }}>
    💬 意见反馈
  </button>
  ```
- 顶部声明 `const [showFeedbackDialog, setShowFeedbackDialog] = useState(false)`
- 渲染 `<FeedbackDialog open={showFeedbackDialog} onClose={...} />`

#### 6.3.2 `FeedbackDialog` 组件

`frontend/components/FeedbackDialog.tsx`：

- props: `open: boolean`, `onClose: () => void`
- 内部状态：`content` (受控 textarea, maxLength=1000), `images: File[]`, `submitting`, `error`
- 提交流程：
  1. 客户端预校验 `content.trim().length` ∈ [1, 1000]，`images.length <= 5`
  2. `submitFeedback({content, images})`
  3. 成功：toast（用最小化的顶部 banner，避免引入新组件） + 关闭 dialog + 清空
  4. 失败：显示后端错误
- 附件选择：`<input type="file" multiple accept="image/png,image/jpeg,image/webp">`，`onChange` 时本地预校验大小/类型，失败 toast 提示后丢弃非法文件
- 缩略图：使用 `URL.createObjectURL(file)`，`useEffect` 清理

`frontend/lib/feedback.ts`（新增）：
- `submitFeedback({content, images}): Promise<{id, created_at}>`
- `adminListFeedback(filters, page, pageSize)`, `adminGetFeedback(id)`, `adminPatchFeedback(id, status)`

#### 6.3.3 admin 反馈模块

新增 `frontend/app/admin/feedback/page.tsx`（列表 + 过滤 + 分页 + 表格），`frontend/app/admin/feedback/[id]/page.tsx`（详情）。

admin layout 导航（仅 admin 分支）：
```ts
{ href: "/admin", label: "概览" },
{ href: "/admin/users", label: "用户管理" },
{ href: "/admin/feedback", label: "意见反馈" },  // 新增
{ href: "/admin/conversations", label: "对话历史" },
```

admin 概览页（`frontend/app/admin/page.tsx`）的 modules 列表增加第三项：
```ts
{
  title: "意见反馈",
  description: "查看用户提交的意见与建议,支持附件下载。",
  href: "/admin/feedback",
}
```

列表页：
- stat cards：总数 / 未读(open) / 已读(read) / 已处理(resolved)
- 状态 tab（点击切换 `status` 过滤 + 跳回第 1 页）
- 关键词搜索框（防抖 300ms）
- 表格列：提交时间 / 提交人 / 摘要前 30 字 / 附件数 / 状态 badge / 操作（查看 / 一键标记已处理）
- 分页器复用阶段 1 的实现（提取 `AdminPagination` 组件）

详情页：
- 顶部信息条：提交时间、状态 badge、IP、UA
- 提交人卡片：工号 / 姓名 / 邮箱 / 一级部门 / 主角色
- 完整内容：`<pre>` 保留换行
- 附件：缩略图网格，点击在新窗口打开
- 底部操作：「标记为已读」（仅 open 状态显示）/「标记为已处理」（仅 open/read 状态显示）

## 7. 隔离与复用

- `app/services/feedback_service.py` 与 `app/services/conversation_summary_service.py` 各自独立单元，单元测试可以独立 mock。
- `frontend/components/FeedbackDialog.tsx` 不依赖任何 admin 状态，可被聊天页直接 import；与 admin 的 `FeedbackList` 不共享代码。
- 分页器在阶段 1 写一个 `frontend/components/admin/AdminPagination.tsx`，阶段 3 复用；不引入通用分页库（避免一次性引入依赖）。
- localStorage 键集中：`admin.users.pageSize`, `admin.feedback.pageSize`，避免冲突。

## 8. 错误处理

| 场景 | 表现 |
|---|---|
| 关键词超长 / 非法页码 | 后端夹紧到合法范围；前端不做硬校验 |
| 反馈图片超 3MB | 后端 413；前端预校验先拦下，给出 toast |
| 反馈图片非 png/jpg/jpeg/webp | 后端 422；前端预校验先拦下 |
| 反馈文本超 1000 字 | 前端 `maxLength` 拦下 |
| 反馈文本为空 | 前端提交按钮 disabled |
| AI 速览 LLM 调用失败 | 后端 502；前端在速览面板内显示「生成失败，点击重试」 |
| 教练访问无权限的会话速览 | 后端 403；前端不显示按钮（详情未加载就无按钮） |
| 反馈详情已被另一个 admin 标记为已读 | 不影响；状态机幂等（open→read 只能发生一次） |

## 9. 测试计划

### 9.1 后端单测

- `test_admin_users_pagination.py`：
  - 第 1 页/第 2 页/最后一页边界
  - `page_size` 超过 100 夹紧
  - 过滤 + 分页组合的 `total` 正确
  - 排序稳定（同一 `updated_at` 的两条记录顺序确定）
- `test_conversation_summary_service.py`：
  - 短会话（< 30 条）全量发送
  - 长会话（> 30 条）截断为 5+25
  - 权限：教练被拒
- `test_feedback_service.py`：
  - 正常提交（文本 + 3 张图）
  - 文本超 1000 字符 → 422
  - 第 6 张图 → 422
  - 4MB 图片 → 413
  - 非法扩展名（.gif）→ 422
  - 列表分页 / 状态过滤 / 关键词搜索
  - 状态机：open→read 自动写 read_at；read→resolved 写 resolved_at；resolved→read 不动 resolved_at

### 9.2 前端单测 / 集成

不强制；`tsc --noEmit` + 手动验证 + 已有 Playwright（如可用）。

### 9.3 端到端验证（手动）

- 阶段 1：导入 ≥ 50 个 managed_user（用现有 Excel 导入路径），验证分页 / 过滤 / 重置 / 页大小持久化
- 阶段 2：构造一个含 50+ 消息的会话，验证按钮位置、生成结果、上下分布局、重新生成、关闭 dialog 后状态清空
- 阶段 3：admin 提交一个含 5 张图、999 字的反馈，admin 后台列表 → 详情 → 状态切换；再以普通用户身份验证无法访问 `/admin/feedback`

## 10. 实施顺序

### 阶段 1 — 用户管理（分页 + 过滤）

1. 后端 `list_managed_users` 加分页参数与响应结构
2. 前端 `lib/admin.ts` + `types/admin.ts` 调整
3. `admin/users/page.tsx` 工具栏 + 分页器
4. 验证 + commit

### 阶段 2 — AI 速览

1. 后端 `conversation_summary_service` + 路由
2. 前端 `lib/admin.ts` + `types/admin.ts`
3. `admin/conversations/page.tsx` dialog 头部按钮 + 详情面板上下分
4. 验证 + commit

### 阶段 3 — 意见反馈

1. 迁移 + model
2. 后端 feedback service + 路由（POST / GET list / GET detail / PATCH）
3. 静态文件路由 + prefix 校验
4. 聊天页 `FeedbackDialog` 组件
5. admin 列表 + 详情页
6. admin 导航 + 概览卡片
7. 验证 + commit

## 11. 不在本次范围

- 移动端响应式优化（PC only，按用户确认）
- 反馈 markdown 渲染（用户确认不需要）
- 反馈通知机制（用户确认不提醒）
- 反馈导出 / 搜索提交人邮箱 / IP 限流
- AI 速览的批量生成或多会话摘要
- 反馈评论 / 处理记录（admin 与用户对话）
