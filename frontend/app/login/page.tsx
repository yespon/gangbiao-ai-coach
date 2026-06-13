"use client";

import { FormEvent, useEffect, useState } from "react";
import { casExchange, getCasLoginUrl, login, checkAuth, hasSessionHint, getAuthConfig } from "@/lib/auth";
import Link from "next/link";

type LoginMode = "choice" | "exchanging" | "local";

export default function LoginPage() {
  const [mode, setMode] = useState<LoginMode>("choice");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [statusText, setStatusText] = useState("");

  const [authMode, setAuthMode] = useState("both");

  const localDisabled = authMode === "sso";
  const ssoDisabled = authMode === "local";

  useEffect(() => {
    // If already authenticated (e.g. has session cookie), redirect to home
    if (hasSessionHint()) {
      checkAuth().then((user) => {
        if (user) window.location.href = "/";
      });
    }

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

  async function handleCasExchange(ticket: string) {
    setBusy(true);
    setError("");
    try {
      await casExchange(ticket);
      // Cookie is set by the response, redirect to home
      // Clean ticket from URL first
      window.history.replaceState({}, "", "/login");
      window.location.href = "/";
    } catch (err) {
      setError(err instanceof Error ? err.message : "SSO 登录失败");
      setMode("choice");
    } finally {
      setBusy(false);
      setStatusText("");
    }
  }

  function handleSsoLogin() {
    // Redirect to CAS login endpoint (which redirects to SID)
    window.location.href = getCasLoginUrl();
  }

  async function handleLocalSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      await login(email, password);
      window.location.href = "/";
    } catch (err) {
      setError(err instanceof Error ? err.message : "登录失败");
    } finally {
      setBusy(false);
    }
  }

  // Exchanging CAS ticket — show spinner
  if (mode === "exchanging") {
    return (
      <main className="auth-page">
        <div className="auth-card">
          <h1 className="auth-title">登录中</h1>
          <div className="auth-status">
            <div className="auth-spinner" />
            <p className="auth-status-text">{statusText}</p>
          </div>
          {error && <div className="auth-error">{error}</div>}
        </div>
      </main>
    );
  }

  return (
    <main className="auth-page">
      <div className="auth-card">
        <h1 className="auth-title">岗标AI教练</h1>

        {/* SSO Login — primary action */}
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

        {/* Divider */}
        {mode === "choice" && (
          <div className="auth-divider">
            <span>或</span>
          </div>
        )}

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

        {mode === "local" && (
          <>
            <form onSubmit={handleLocalSubmit} className="auth-form">
              <label className="auth-label">
                邮箱
                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  required
                  autoComplete="email"
                  className="auth-input"
                  placeholder="your@email.com"
                />
              </label>
              <label className="auth-label">
                密码
                <div className="auth-password-row">
                  <input
                    type={showPassword ? "text" : "password"}
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    required
                    autoComplete="current-password"
                    className="auth-input"
                    placeholder="密码"
                  />
                  <button
                    type="button"
                    className="auth-toggle-pw"
                    onClick={() => setShowPassword(!showPassword)}
                    aria-label={showPassword ? "隐藏密码" : "显示密码"}
                  >
                    {showPassword ? "隐" : "显"}
                  </button>
                </div>
              </label>
              <button type="submit" disabled={busy} className="auth-btn">
                {busy ? "登录中..." : "登录"}
              </button>
            </form>
            <p className="auth-link-text">
              还没有账号？<Link href="/register">立即注册</Link>
            </p>
            <button
              type="button"
              className="auth-back-link"
              onClick={() => setMode("choice")}
            >
              ← 返回
            </button>
          </>
        )}

        {error && <div className="auth-error">{error}</div>}
      </div>
    </main>
  );
}
