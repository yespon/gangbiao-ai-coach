# CAS SSO 生产部署清单

> 适用于 Ruijie SID (CAS) + PostgreSQL 服务端 session 方案
> 配套文档：`docs/cas-sso-local-debugging-guide.md`（本地调试）

---

## 0. 部署架构假设

```
                 ┌─────────────────────────────────────┐
   浏览器 ──────► │  nginx (443, HTTPS)                 │
                 │   ├── /api/  → backend:2024          │  ← 同域反代
                 │   └── /      → frontend:3000         │
                 └─────────────────────────────────────┘
                              │
   SID 后端 ──POST /api/v1/cas/slo──► backend  (BACK_CHANNEL，不经浏览器)
```

**关键约束：前端与后端必须同域**（通过 nginx 同一域名反代 `/` 和 `/api/`）。
session cookie 用 `SameSite=Lax`，依赖前后端同站。若拆成 `app.x.com` + `api.x.com`
两个域，必须改为 `SameSite=None` 且 cookie 域设为父域 `.x.com`。

---

## 1. 前置依赖（外部，非代码）

| # | 事项 | 负责方 | 阻塞点 |
|---|------|--------|--------|
| 1 | 飞书应用注册审批通过 | 申请方 | ⚠️ 完整 SSO 联调的前提 |
| 2 | SID 侧登记 service URL 白名单 | SID 管理员 | service URL 必须**完全一致** |
| 3 | SID 侧配置 SLO BACK_CHANNEL 回调地址 | SID 管理员 | 指向 `https://<域名>/api/v1/cas/slo` |
| 4 | 生产域名 + HTTPS 证书 | 运维 | SID 要求 HTTPS |
| 5 | 出口 IP 在 SID 白名单内 | 运维 | 否则 `/p3/serviceValidate` 连不通 |

---

## 2. 环境变量（生产 `.env`）

> ⚠️ `.env` 已被 `.gitignore`，**严禁提交**。生产值由运维在部署机单独配置。
> JWT 相关配置已移除（认证全面改用服务端 session）。

```bash
# ---------- LLM ----------
OPENAI_API_KEY=<生产密钥>
OPENAI_BASE_URL=https://uniapi.ruijie.com.cn/v1
OPENAI_MODEL=gpt-5.5

# ---------- 数据库 ----------
POSTGRES_PASSWORD=<强密码，勿用 gangbiao_dev>
DATABASE_URL=postgresql+asyncpg://gangbiao:<强密码>@postgres:5432/gangbiao

# ---------- CORS ----------
# 生产域名（HTTPS，无端口）
CORS_ALLOW_ORIGINS=https://gangbiao-ai-coach.ruijie.com.cn

# ---------- CAS / SSO ----------
AUTH_MODE=both                                              # 过渡期 both；切换完成后改 sso
SID_BASE_URL=https://sid.ruijie.com.cn
SID_SERVICE_URL=https://gangbiao-ai-coach.ruijie.com.cn/login   # 必须与 SID 白名单完全一致
SID_LOGOUT_URL=https://sid.ruijie.com.cn/logout
SESSION_COOKIE_NAME=sid_session
SESSION_COOKIE_SECURE=true                                 # ⚠️ 生产 HTTPS 必须 true
SESSION_COOKIE_SAMESITE=Lax                                # 前后端同域用 Lax
SESSION_TTL_HOURS=8                                        # 对齐 SID TGC 8h
SESSION_SLIDING_REFRESH_MINUTES=30
CAS_VALIDATE_TIMEOUT_SECONDS=5                             # ST 仅 10s，超时要短

# ---------- CSRF ----------
CSRF_COOKIE_NAME=csrf_token
CSRF_HEADER_NAME=X-CSRF-Token

# ---------- Session 清理 ----------
SESSION_CLEANUP_INTERVAL_MINUTES=60
SESSION_CLEANUP_GRACE_DAYS=1
```

### 本地 vs 生产差异（最易踩坑）

| 配置 | 本地调试 | 生产 |
|------|---------|------|
| `SESSION_COOKIE_SECURE` | `false`（HTTP） | **`true`**（HTTPS） |
| `SID_SERVICE_URL` | `http://...:2088/login` | `https://.../login`（标准 443，无端口） |
| `CORS_ALLOW_ORIGINS` | 含 `:2088` 端口 | HTTPS 域名，无端口 |
| `AUTH_MODE` | `both` | 过渡期 `both` → 最终 `sso` |

---

## 3. nginx 生产配置（HTTPS）

当前 `deploy/nginx/default.conf` 只监听 80。生产需改为 443 + 证书 +
传递 `X-Forwarded-Proto`（后端据此判断 HTTPS，cookie Secure 才正确）：

```nginx
server {
    listen 80;
    server_name gangbiao-ai-coach.ruijie.com.cn;
    return 301 https://$host$request_uri;        # 强制跳 HTTPS
}

server {
    listen 443 ssl;
    server_name gangbiao-ai-coach.ruijie.com.cn;

    ssl_certificate     /etc/nginx/certs/fullchain.pem;
    ssl_certificate_key /etc/nginx/certs/privkey.pem;

    client_max_body_size 50m;
    resolver 127.0.0.11 ipv6=off valid=30s;

    location /api/ {
        set $backend_upstream http://backend:2024;
        proxy_pass $backend_upstream;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;       # ⚠️ 必须，cookie Secure 依赖
    }

    location / {
        set $frontend_upstream http://frontend:3000;
        proxy_pass $frontend_upstream;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_cache_bypass $http_upgrade;
    }
}
```

`docker-compose.yml` 的 nginx 服务还需暴露 443 并挂载证书：
```yaml
  nginx:
    ports:
      - "443:443"
      - "80:80"
    volumes:
      - ./deploy/nginx/default.conf:/etc/nginx/conf.d/default.conf:ro
      - ./deploy/nginx/certs:/etc/nginx/certs:ro
```

---

## 4. 数据库迁移

迁移在 backend 容器启动时自动执行（`Dockerfile.backend` CMD 已含
`alembic upgrade head`）。涉及的迁移：

| Revision | 内容 |
|----------|------|
| `001_initial` | users / chat_sessions / chat_messages（原有） |
| `002_auth_sessions` | **新增** auth_sessions 表 + 索引 |
| `003_user_sso_fields` | **新增** users.provider / provider_user_id；email、password_hash 改为 nullable |

> ⚠️ `003` 把 `users.email` / `password_hash` 改为可空。回滚 `003` 前需确认
> 没有 email 为 NULL 的 SSO 用户，否则 `NOT NULL` 约束会失败。

部署前建议手动验证：
```bash
docker compose exec backend alembic current        # 确认当前版本
docker compose exec backend alembic upgrade head    # 手动执行（可选）
docker compose exec postgres psql -U gangbiao -d gangbiao -c "\d auth_sessions"
```

---

## 5. 部署步骤

```bash
# 1. 拉取代码
git pull origin main

# 2. 配置生产 .env（参照第 2 节，勿提交）
vim .env

# 3. 配置 nginx HTTPS + 证书（第 3 节）
cp fullchain.pem privkey.pem deploy/nginx/certs/

# 4. 构建并启动（--build 确保镜像最新）
docker compose up -d --build

# 5. 确认迁移已执行
docker compose logs backend | grep -i alembic

# 6. 健康检查
curl -fsS https://gangbiao-ai-coach.ruijie.com.cn/api/v1/health
```

> ⚠️ **改 `.env` 后必须 `docker compose up -d`（重建容器），不能用 `restart`** —
> `restart` 不重新加载 `env_file`，旧环境变量会残留。

---

## 6. 上线后验收检查清单

| # | 检查项 | 命令 / 操作 | 预期 |
|---|--------|------------|------|
| 1 | HTTPS 健康检查 | `curl -fsS https://<域名>/api/v1/health` | `{"status":"ok"}` |
| 2 | 登录页 SSO 入口 | 浏览器打开 `/login` | 显示「企业 SSO 登录」 |
| 3 | SSO 跳转 | 点击 SSO 按钮 | 跳转到 SID 登录页 |
| 4 | SID 回调 | SID 登录后 | 回 `/login?ticket=ST-xxx` |
| 5 | ticket 交换 | 自动 | 设置 `sid_session` cookie，跳首页 |
| 6 | cookie 鉴权 | 已登录访问 `/api/v1/auth/me` | 200 + 用户信息 |
| 7 | cookie 安全属性 | DevTools → Cookies | `sid_session` 为 HttpOnly + Secure |
| 8 | CSRF 写保护 | 删 `X-CSRF-Token` 发 POST | 403 |
| 9 | SLO | SID 触发单点登出 | session 被 revoke，`/me` 返回 401 |
| 10 | session 落库 | `SELECT count(*) FROM auth_sessions` | 有记录 |
| 11 | 过期清理 | 等待 cleanup 周期 | 过期 session 被删除 |

---

## 7. AUTH_MODE 切换策略（过渡 → SSO-only）

```
阶段一（上线初期）  AUTH_MODE=both
  ├── 本地账号密码登录可用（存量用户）
  └── SSO 登录可用（新用户）

阶段二（全员迁移后）AUTH_MODE=sso
  ├── /auth/register、/auth/login 返回 403
  ├── 仅 SSO 入口可登录
  └── /auth/me、/auth/logout 仍可用（两模式都需要）
```

切换只需改 `.env` 的 `AUTH_MODE=sso` 后 `docker compose up -d backend`，无需改代码。

---

## 8. 回滚预案

| 场景 | 操作 |
|------|------|
| SSO 联调失败，需回退到账号密码 | `.env` 设 `AUTH_MODE=local` + `docker compose up -d backend` |
| 需回退代码 | `git revert` 或切回上一 tag，`docker compose up -d --build` |
| 迁移出错 | `alembic downgrade 001_initial`（注意第 4 节 003 回滚约束） |

---

## 9. 安全要点回顾

- ✅ session token 在 DB 存 SHA-256 哈希，cookie 存原始 token（泄库不可逆推）
- ✅ session cookie：HttpOnly（防 XSS 读取）+ Secure（仅 HTTPS）+ SameSite=Lax（防 CSRF 导航）
- ✅ CSRF 双重提交：写操作校验 `X-CSRF-Token` header == `csrf_token` cookie
- ✅ `/cas/exchange`、`/auth/login` 不挂 CSRF（首次登录无 token，合理）
- ✅ `/cas/slo` 不挂 CSRF（SID 后端调用，无浏览器 cookie）
- ⚠️ 生产务必更换 `POSTGRES_PASSWORD`（勿用 `gangbiao_dev`）
- ⚠️ `OPENAI_API_KEY` 等密钥仅存生产 `.env`，永不入库
