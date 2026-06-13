# 登录页 auth_mode 开关（置灰禁用入口）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 通过公开 config 端点把 `auth_mode` 暴露给登录页，前端据此对称置灰被禁用的登录入口（sso→灰账号密码、local→灰 SSO）。

**Architecture:** 复用后端既有 `settings.auth_mode`，新增 1 个公开 GET 端点；前端登录页挂载时拉取，派生 `localDisabled`/`ssoDisabled` 控制按钮 disabled + 提示。拉取失败降级为都可用（后端 403 兜底）。

**Tech Stack:** FastAPI（后端，pytest 有框架→真 TDD）；Next.js 15 + React + TS（前端，无单测框架→tsc + build + 手动 E2E）。

**基准：** 后端测试在仓库根目录用 `.venv/bin/python -m pytest`；前端命令在 `frontend/` 下。spec 见 `docs/superpowers/specs/2026-06-13-disable-local-login-toggle-design.md`。

---

## File Structure

| 文件 | 责任 | 动作 |
|------|------|------|
| `app/api/v1/routes/auth.py` | 新增 `GET /config` 返回 `{auth_mode}` | Modify |
| `tests/integration/test_auth_config.py` | config 端点测试 | Create |
| `frontend/lib/auth.ts` | 新增 `getAuthConfig()` | Modify |
| `frontend/app/login/page.tsx` | 拉取 auth_mode + 对称置灰按钮 + 提示 | Modify |
| `frontend/app/globals.css` | `.auth-local-toggle:disabled` + `.auth-disabled-hint` | Modify |

---

## Task 1: 后端 `/auth/config` 端点（TDD）

**Files:**
- Test: `tests/integration/test_auth_config.py`
- Modify: `app/api/v1/routes/auth.py`

- [ ] **Step 1: 写失败测试 `tests/integration/test_auth_config.py`**

```python
"""GET /auth/config exposes auth_mode to the login page (pre-auth, public)."""


def test_auth_config_returns_mode(client):
    resp = client.get("/api/v1/auth/config")
    assert resp.status_code == 200
    body = resp.json()
    assert "auth_mode" in body
    assert body["auth_mode"] in ("sso", "local", "both")


def test_auth_config_reflects_setting(client, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "auth_mode", "sso")
    resp = client.get("/api/v1/auth/config")
    assert resp.status_code == 200
    assert resp.json()["auth_mode"] == "sso"
```

> 用现有 `client` fixture（已 override get_current_user/get_db/verify_csrf）。`/config` 不依赖 db/鉴权，fixture 适用，无需 PG。

- [ ] **Step 2: 运行测试，确认失败**

Run: `.venv/bin/python -m pytest tests/integration/test_auth_config.py -v`
Expected: FAIL（404 Not Found，端点尚不存在）

- [ ] **Step 3: 在 `app/api/v1/routes/auth.py` 新增端点**

在 `@router.get("/me", ...)` 定义之前（约第 153 行前）加入：

```python
@router.get("/config")
async def auth_config():
    """Public auth config for the login page (pre-auth). Non-sensitive."""
    return {"auth_mode": settings.auth_mode}
```

> `settings` 已在该文件顶部导入（`from app.core.config import settings`）。

- [ ] **Step 4: 运行测试，确认通过**

Run: `.venv/bin/python -m pytest tests/integration/test_auth_config.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: 跑完整后端测试确认无回归**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: 全部通过（此前 40 passed 基线 + 新增 2）

- [ ] **Step 6: Commit**

```bash
git add app/api/v1/routes/auth.py tests/integration/test_auth_config.py
git commit -m "feat(auth): public GET /auth/config exposing auth_mode"
```

---

## Task 2: 前端 `getAuthConfig()`

**Files:**
- Modify: `frontend/lib/auth.ts`

- [ ] **Step 1: 在 `frontend/lib/auth.ts` 的 `getCasLoginUrl` 之后加入 `getAuthConfig`**

找到现有：

```ts
/** Get CAS login redirect URL (backend will 302 to SID). */
export function getCasLoginUrl(): string {
  return `${API_BASE}/api/v1/cas/login`;
}
```

在其后加入：

```ts
/** Fetch public auth config (auth_mode) for the login page. */
export async function getAuthConfig(): Promise<{ auth_mode: string }> {
  const resp = await fetch(`${API_BASE}/api/v1/auth/config`);
  if (!resp.ok) throw new Error("config unavailable");
  return resp.json();
}
```

- [ ] **Step 2: 类型检查**

Run: `cd frontend && npx tsc --noEmit`
Expected: 无错误

- [ ] **Step 3: Commit**

```bash
git add frontend/lib/auth.ts
git commit -m "feat(ui): add getAuthConfig() to fetch auth_mode"
```

---

## Task 3: 登录页对称置灰按钮 + 提示

**Files:**
- Modify: `frontend/app/login/page.tsx`

- [ ] **Step 1: import `getAuthConfig`**

把第 4 行：

```tsx
import { casExchange, getCasLoginUrl, login, checkAuth, hasSessionHint } from "@/lib/auth";
```

改为：

```tsx
import { casExchange, getCasLoginUrl, login, checkAuth, hasSessionHint, getAuthConfig } from "@/lib/auth";
```

- [ ] **Step 2: 加 `authMode` state + 派生布尔**

把第 16 行 `const [statusText, setStatusText] = useState("");` 之后加：

```tsx
  const [authMode, setAuthMode] = useState("both");

  const localDisabled = authMode === "sso";
  const ssoDisabled = authMode === "local";
```

- [ ] **Step 3: 挂载时拉取 auth_mode（降级保持 both）**

把现有 `useEffect`（第 18-34 行）末尾、`}, []);` 之前加入拉取逻辑。将：

```tsx
    // Check for CAS ticket in URL (redirect back from SID)
    const params = new URLSearchParams(window.location.search);
    const ticket = params.get("ticket");
    if (ticket) {
      setMode("exchanging");
      setStatusText("正在验证企业 SSO 登录...");
      handleCasExchange(ticket);
    }
  }, []);
```

改为：

```tsx
    // Check for CAS ticket in URL (redirect back from SID)
    const params = new URLSearchParams(window.location.search);
    const ticket = params.get("ticket");
    if (ticket) {
      setMode("exchanging");
      setStatusText("正在验证企业 SSO 登录...");
      handleCasExchange(ticket);
    }

    // Fetch auth_mode to grey out disabled entry; fall back to "both" on error
    getAuthConfig()
      .then((cfg) => setAuthMode(cfg.auth_mode))
      .catch(() => setAuthMode("both"));
  }, []);
```

- [ ] **Step 4: SSO 按钮 disabled + 提示**

把 SSO 按钮（第 95-107 行）：

```tsx
        <button
          type="button"
          className="auth-sso-btn"
          onClick={handleSsoLogin}
          disabled={busy}
        >
          <svg className="auth-sso-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4" />
            <polyline points="10 17 15 12 10 7" />
            <line x1="15" y1="12" x2="3" y2="12" />
          </svg>
          企业 SSO 登录
        </button>
```

改为：

```tsx
        <button
          type="button"
          className="auth-sso-btn"
          onClick={handleSsoLogin}
          disabled={busy || ssoDisabled}
        >
          <svg className="auth-sso-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4" />
            <polyline points="10 17 15 12 10 7" />
            <line x1="15" y1="12" x2="3" y2="12" />
          </svg>
          企业 SSO 登录
        </button>
        {ssoDisabled && mode === "choice" ? (
          <div className="auth-disabled-hint">管理员已禁用 SSO 登录</div>
        ) : null}
```

- [ ] **Step 5: 账号密码 toggle 按钮 disabled + 提示**

把 local toggle 块（第 116-125 行）：

```tsx
        {/* Local login toggle / form */}
        {mode === "choice" && (
          <button
            type="button"
            className="auth-local-toggle"
            onClick={() => setMode("local")}
          >
            使用账号密码登录
          </button>
        )}
```

改为：

```tsx
        {/* Local login toggle / form */}
        {mode === "choice" && (
          <>
            <button
              type="button"
              className="auth-local-toggle"
              onClick={() => setMode("local")}
              disabled={localDisabled}
            >
              使用账号密码登录
            </button>
            {localDisabled ? (
              <div className="auth-disabled-hint">管理员已禁用账号密码登录</div>
            ) : null}
          </>
        )}
```

- [ ] **Step 6: 类型检查**

Run: `cd frontend && npx tsc --noEmit`
Expected: 无错误

- [ ] **Step 7: Commit**

```bash
git add frontend/app/login/page.tsx
git commit -m "feat(ui): grey out disabled login entry based on auth_mode"
```

---

## Task 4: CSS（disabled 灰态 + 提示样式）

**Files:**
- Modify: `frontend/app/globals.css`

- [ ] **Step 1: 在 `.auth-local-toggle:hover` 之后加 disabled 态与提示样式**

找到现有（约第 780-783 行）：

```css
.auth-local-toggle:hover {
  background: var(--bg);
  color: var(--ink);
}
```

在其后加入：

```css
.auth-local-toggle:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.auth-local-toggle:disabled:hover {
  background: transparent;
  color: var(--muted);
}

.auth-disabled-hint {
  margin-top: 8px;
  text-align: center;
  font-size: 12px;
  color: var(--muted);
}
```

> `.auth-sso-btn:disabled` 已存在（含 opacity 0.6 + not-allowed），无需新增。

- [ ] **Step 2: 构建检查**

Run: `cd frontend && npx next build`
Expected: 构建成功

- [ ] **Step 3: Commit**

```bash
git add frontend/app/globals.css
git commit -m "feat(ui): disabled style for local-login toggle + hint text"
```

---

## Task 5: 验证 + 手动 E2E

- [ ] **Step 1: 类型检查 + 构建**

Run: `cd frontend && npx tsc --noEmit && npx next build`
Expected: tsc 无错误；build 成功

- [ ] **Step 2: 手动 E2E（重建 Docker backend 切换 AUTH_MODE）**

逐项确认（每次改 `.env` 的 `AUTH_MODE` 后 `docker compose up -d --build backend`，前端无需重建）：

1. `AUTH_MODE=both`（默认）→ 登录页两按钮都可用，无提示
2. `AUTH_MODE=sso` → 「使用账号密码登录」置灰不可点 + 「管理员已禁用账号密码登录」提示；SSO 按钮可用
3. `AUTH_MODE=local` → 「企业 SSO 登录」置灰不可点 + 「管理员已禁用 SSO 登录」提示；账号密码可用
4. 停掉 backend（`docker compose stop backend`）刷新登录页 → config 拉取失败，两按钮降级为可用（不误伤）

> 验证完把 `.env` 的 `AUTH_MODE` 改回 `both` 并重建 backend。

- [ ] **Step 3: 无独立 commit（验证步骤）**

---

## Self-Review 结果

- **Spec 覆盖：** config 端点(T1)、getAuthConfig(T2)、对称置灰+提示+降级(T3)、CSS disabled+hint(T4)、验证含 4 场景(T5) — 全覆盖。
- **占位符扫描：** 无 TBD/TODO；每个代码步骤含完整代码。
- **类型/命名一致性：** `auth_mode`(后端 dict key) ↔ `getAuthConfig(): {auth_mode}`(T2) ↔ `cfg.auth_mode`(T3) 一致；`localDisabled`/`ssoDisabled`(T3 派生) 与按钮 disabled 引用一致；CSS 类 `.auth-local-toggle:disabled`/`.auth-disabled-hint`(T4) 与 T3 JSX className 一致。
- **测试策略：** 后端 TDD（pytest 有框架）；前端 tsc+build+手动（无框架，符合既有模式）。
