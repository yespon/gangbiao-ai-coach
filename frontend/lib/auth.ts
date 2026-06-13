import { UserInfo, CASExchangeResponse } from "@/types/auth";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "") || "";

// ---------- CSRF helper ----------

/** Read the non-httpOnly CSRF cookie set by the backend. */
export function getCsrfToken(): string | null {
  if (typeof document === "undefined") return null;
  const match = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]*)/);
  return match ? decodeURIComponent(match[1]) : null;
}

/** Quick synchronous hint: does it look like we have a session? */
export function hasSessionHint(): boolean {
  if (typeof document === "undefined") return false;
  return document.cookie.includes("csrf_token=");
}

// ---------- Auth state check ----------

/**
 * Verify authentication by calling /auth/me with session cookie.
 * Returns user info if authenticated, null otherwise.
 */
export async function checkAuth(): Promise<UserInfo | null> {
  try {
    const resp = await fetch(`${API_BASE}/api/v1/auth/me`, {
      credentials: "include",
    });
    if (resp.ok) return resp.json();
  } catch {
    // Network error
  }
  return null;
}

// ---------- CAS SSO ----------

/**
 * Exchange CAS ticket for server-side session.
 * Session cookie is set automatically by the response.
 */
export async function casExchange(ticket: string): Promise<CASExchangeResponse> {
  const resp = await fetch(`${API_BASE}/api/v1/cas/exchange`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({ ticket }),
  });
  if (!resp.ok) {
    const data = await resp.json().catch(() => ({ detail: "SSO 登录失败" }));
    throw new Error(data.detail || "SSO 登录失败");
  }
  return resp.json();
}

/** Get CAS login redirect URL (backend will 302 to SID). */
export function getCasLoginUrl(): string {
  return `${API_BASE}/api/v1/cas/login`;
}

/** Fetch public auth config (auth_mode) for the login page. */
export async function getAuthConfig(): Promise<{ auth_mode: string }> {
  const resp = await fetch(`${API_BASE}/api/v1/auth/config`);
  if (!resp.ok) throw new Error("config unavailable");
  return resp.json();
}

// ---------- Local auth (email/password) ----------

/**
 * Login with email/password. Session cookie is set automatically.
 */
export async function login(email: string, password: string): Promise<void> {
  const resp = await fetch(`${API_BASE}/api/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({ email, password }),
  });
  if (!resp.ok) {
    const data = await resp.json().catch(() => ({ detail: "登录失败" }));
    throw new Error(data.detail || "登录失败");
  }
  // Session cookie is set by Set-Cookie header — nothing to store.
}

/**
 * Register with email/password. Session cookie is set automatically.
 */
export async function register(email: string, password: string, nickname?: string): Promise<void> {
  const resp = await fetch(`${API_BASE}/api/v1/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({ email, password, nickname }),
  });
  if (!resp.ok) {
    const data = await resp.json().catch(() => ({ detail: "注册失败" }));
    throw new Error(data.detail || "注册失败");
  }
  // Session cookie is set by Set-Cookie header — nothing to store.
}

// ---------- Logout ----------

export async function logout(): Promise<void> {
  try {
    const resp = await fetch(`${API_BASE}/api/v1/auth/logout`, {
      method: "POST",
      credentials: "include",
      redirect: "manual",
    });
    // Backend returns 302 to SID logout — follow it
    if (resp.type === "opaqueredirect" || resp.status === 302) {
      const location = resp.headers.get("location");
      if (location) {
        window.location.href = location;
        return;
      }
    }
  } catch {
    // Network error — just redirect to login
  }
  window.location.href = "/login";
}

// ---------- User info ----------

export async function getUserInfo(): Promise<UserInfo | null> {
  return checkAuth();
}
