# 岗位标准化 Chatbot 服务

## 功能

- 新建会话时自动加载 `岗位标准化母体.history.json` 作为默认上下文。
- 可通过开关控制是否在用户对话记录中显示这些默认上下文。
- 聊天框支持多附件上传，后端会保存附件并把可读文本摘要注入对话。
- 多用户认证：邮箱注册/登录，JWT access + refresh token。
- 聊天会话与消息持久化到 PostgreSQL，用户级数据隔离。
- 未配置 `OPENAI_API_KEY` 时会启用本地回退回复，便于本地联调。

## 架构

- `main.py` + `app/`：FastAPI API-only 后端（会话、聊天、流式 SSE、附件提取）。
- `frontend/`：Next.js 前端（TypeScript + App Router），通过 HTTP 调用后端 API。

## 启动

1. 安装依赖

```bash
uv sync
```

2. 启动 PostgreSQL（本地开发需要）

`ash
docker compose up postgres -d
```

3. 后端地址

- API: `http://127.0.0.1:2024`
- 根路径会返回服务状态 JSON（不再托管静态页面）。

4. 启动前端（Next.js）

```bash
cd frontend
npm install
cp .env.example .env.local
npm run dev
```

5. 打开页面

访问 `http://127.0.0.1:3000`。

## 部署（Docker Compose）

生产部署采用三容器架构：

1. `backend`：FastAPI 服务（端口 `2024`，仅内网）。
2. `frontend`：Next.js 服务（端口 `3000`，仅内网）。
3. `nginx`：统一入口（对外 `2088`），转发 `/api/*` 到后端，其余路径到前端。

> 数据库走外部 k8s PostgreSQL（test / prod 各一套）。base `docker-compose.yml` 不含 `DATABASE_URL`，**必须配合 override 启动**。

### 一次性准备

```bash
# 1) 公共环境变量（CORS/SID/AUTH_MODE/cookie 等，不含 DATABASE_URL）
cp .env.example .env

# 2) 数据库连接（含密码，gitignore，按环境二选一或都准备）
cp .env.test.example .env.test  # 编辑填入测试库密码
cp .env.prod.example .env.prod  # 编辑填入生产库密码
```

### 启动

```bash
# 测试环境
docker compose -f docker-compose.yml -f docker-compose.test.yml up -d --build

# 生产环境
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

后端容器启动时自动 `alembic upgrade head` 在外部库的 `postgres` 默认库建表。

### 验证

```bash
docker compose -f docker-compose.yml -f docker-compose.<env>.yml ps
curl -sS http://127.0.0.1:2088/api/v1/health
curl -sS http://127.0.0.1:2088/api/v1/auth/config   # 应返回 {"auth_mode": ...}
```

### 停止

```bash
docker compose -f docker-compose.yml -f docker-compose.<env>.yml down
```

## 测试（TDD）

本项目按 Red-Green-Refactor 流程推进：

1. 先写失败测试（Red）。
2. 做最小实现让测试通过（Green）。
3. 在全绿后重构并再次回归（Refactor）。

常用命令：

```bash
# 仅首次或依赖更新后执行
uv sync --dev

# 快速回归（unit + integration）
uv run pytest tests/unit tests/integration -q

# 契约测试
uv run pytest tests/contract -q

# 全量测试
uv run pytest -q
```

## 并发容量压测

压测脚本与方案文档：

- `scripts/loadtest/chat_capacity.py`
- `docs/performance/concurrency-capacity-playbook.md`

示例（一次跑三类场景：chat / stream / attachment）：

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

默认门禁：

- chat: p95 <= 8000ms，错误率 <= 1%
- stream: p95 <= 20000ms，首 token p95 <= 4000ms，错误率 <= 1%
- attachment: p95 <= 15000ms，错误率 <= 3%

脚本全部通过时退出码为 0；任一场景未达标时退出码为 2。

## 可选环境变量

- `OPENAI_API_KEY`: OpenAI 或兼容网关 key。
- `OPENAI_BASE_URL`: 兼容 OpenAI 协议的 base URL，默认 `https://api.openai.com/v1`。
- `OPENAI_MODEL`: 模型名，默认 `gpt-4o-mini`。
- `MATERIALS_AUTOLOAD`: 是否自动加载教材目录，默认 `true`。
- `MATERIALS_DIR`: 教材目录路径（支持相对项目根目录），如 `materials`。
- `MATERIALS_MAX_FILES`: 每次会话最多注入材料数，默认 `20`。
- `MATERIALS_MAX_EXCERPT_CHARS`: 每份材料注入的最大字符数，默认 `1200`。
- `ATTACHMENT_EXCERPT_CHARS`: 单个用户附件写入会话元数据的长度；`0` 表示不截断（发送全文），默认 `0`。
- `ATTACHMENT_HINT_CHARS`: 服务端日志提示中的附件摘要长度，默认 `800`。

服务启动时会自动读取项目根目录的 `.env` 文件，例如：

```env
OPENAI_API_KEY=your_key
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o-mini

MATERIALS_AUTOLOAD=true
MATERIALS_DIR=materials
MATERIALS_MAX_FILES=20
MATERIALS_MAX_EXCERPT_CHARS=1200
ATTACHMENT_EXCERPT_CHARS=0
ATTACHMENT_HINT_CHARS=800
```
