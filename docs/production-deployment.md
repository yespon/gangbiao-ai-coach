# 生产部署与数据安全指南

## 部署前检查清单

### 1. 数据备份

```bash
# 执行全量备份（数据库 + 文件 + 证书 + 配置）
./scripts/backup_db.sh

# 验证备份可读
pg_restore --list backups/$(date +%Y%m%d)/db_*.dump | head -20
```

### 2. 迁移预检

```bash
# 检查迁移状态是否与数据库一致
alembic check

# 预览将要执行的 SQL（不实际执行）
alembic upgrade head --sql

# 手动执行迁移（推荐，比容器自动执行更可控）
alembic upgrade head
```

> ⚠️ Dockerfile CMD 是 `alembic upgrade head && uvicorn`，容器启动时自动跑迁移。
> 首次部署建议先手动执行迁移确认成功，再启动容器。

### 3. 证书与配置

```bash
# 检查 TLS 证书有效期
openssl x509 -enddate -noout -in deploy/nginx/certs/fullchain.pem

# 确认 .env 配置完整
grep -E "DATABASE_URL|OPENAI_API_KEY|SECRET_KEY|SID_BASE_URL" .env
```

### 4. 健康检查

部署后验证所有端点正常：

```bash
# 后端健康（含 DB 连通检查）
curl -s https://gangbiao-ai-coach.ruijie.com.cn/api/v1/health
# 期望: {"status":"ok"}    DB 异常时: {"status":"degraded","detail":"db_unavailable: ..."}

# 前端页面
curl -sI https://gangbiao-ai-coach.ruijie.com.cn/ | head -5

# Docker 容器状态
docker compose ps
```

## 部署步骤

```bash
# 1. 全量备份
./scripts/backup_db.sh

# 2. 构建并启动
docker compose build
docker compose up -d

# 3. 等待健康检查通过
docker compose ps  # 所有服务 healthy

# 4. 端到端验证
# - 登录（SSO）
# - 创建会话 & 发送消息
# - 上传 Excel 附件
# - 置顶 / 重命名 / 删除会话
```

## 回滚方案

| 场景 | 操作 |
|------|------|
| 迁移失败 | 手动 `alembic downgrade <target>`；005 downgrade 会删数据，优先从备份恢复 |
| 服务异常 | `docker compose down` → `pg_restore` → `docker compose up -d` |
| 数据库丢失 | 停服务 → `pg_restore -c -d gangbiao backups/YYYYMMDD/db_*.dump` → 重启 |
| 证书丢失 | `tar xzf backups/YYYYMMDD/certs_*.tar.gz -C deploy/nginx/` → 重启 nginx |
| 配置丢失 | `cp backups/YYYYMMDD/env_*.bak .env` → 重启 |

### 完整数据库恢复

```bash
# 停止后端
docker compose stop backend

# 恢复数据库
pg_restore \
  --host=<DB_HOST> \
  --port=5432 \
  --username=gangbiao \
  --dbname=gangbiao \
  --clean \
  --if-exists \
  backups/YYYYMMDD/db_*.dump

# 重启
docker compose start backend
```

## 定时备份

添加 cron 实现每日自动备份：

```bash
# 编辑 crontab
crontab -e

# 每日 03:00 执行备份
0 3 * * * cd /app && ./scripts/backup_db.sh >> /app/logs/backup.log 2>&1
```

默认保留 7 天，可通过环境变量调整：

```bash
RETAIN_DAYS=30 ./scripts/backup_db.sh
```

## 数据表关键程度

| 级别 | 表 | 说明 |
|------|-----|------|
| 🔴 不可重建 | users, auth_sessions, chat_sessions, chat_messages | 用户对话核心数据 |
| 🟡 可部分重建 | managed_users, sso_user_whitelist | 需人工重新录入 |
| 🟢 可重建 | feedback_submissions, feedback_attachments | 非核心反馈数据 |
