# 岗位标准化 Chatbot 服务

## 功能

- 新建会话时自动加载 `岗位标准化母体.history.json` 作为默认上下文。
- 可通过开关控制是否在用户对话记录中显示这些默认上下文。
- 聊天框支持多附件上传，后端会保存附件并把可读文本摘要注入对话。
- 已支持自动提取可读文本的附件类型：`txt`、`md`、`json`、`csv`、`doc`、`docx`、`xls`、`xlsx`、`pdf`。
- 支持配置教材目录自动注入：每次新建会话时自动扫描目录并加载可读摘要（无需手工重复上传）。
- 未配置 `OPENAI_API_KEY` 时会启用本地回退回复，便于本地联调。

## 架构

- `main.py` + `app/`：FastAPI API-only 后端（会话、聊天、流式 SSE、附件提取）。
- `frontend/`：Next.js 前端（TypeScript + App Router），通过 HTTP 调用后端 API。

## 启动

1. 安装依赖

```bash
uv sync
```

2. 启动后端服务

```bash
uv run python main.py
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

部署步骤：

```bash
# 1) 准备后端环境变量
cp .env.example .env

# 2) 构建并启动
docker compose up -d --build

# 3) 查看状态
docker compose ps
```

验证：

```bash
curl -sS http://127.0.0.1:2088/api/health
curl -sS http://127.0.0.1:2088/
```

停止：

```bash
docker compose down
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
