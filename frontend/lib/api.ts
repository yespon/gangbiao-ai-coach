import { ChatResponse, SessionResponse, SessionSummary, StreamEvent } from "@/types/chat";

// Default to same-origin API routes to avoid CORS/preflight issues in Docker or LAN access.
const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "") || "";

function endpoint(path: string): string {
  return `${API_BASE}${path}`;
}

function randomUUID(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  // Fallback for HTTP (non-secure) contexts where randomUUID is unavailable.
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
  });
}

/**
 * Returns a stable per-browser user ID persisted in localStorage.
 * When authentication is added, replace this with the real user ID from the
 * auth token/session instead of reading from localStorage.
 */
export function getUserId(): string {
  const KEY = "gb_user_id";
  if (typeof window === "undefined") return "anonymous";
  let uid = localStorage.getItem(KEY);
  if (!uid) {
    uid = randomUUID();
    localStorage.setItem(KEY, uid);
  }
  return uid;
}

function userHeaders(): Record<string, string> {
  return { "X-User-ID": getUserId() };
}

export async function createSession(showContextInHistory: boolean): Promise<SessionResponse> {
  const response = await fetch(endpoint("/api/v1/sessions"), {
    method: "POST",
    headers: { "Content-Type": "application/json", ...userHeaders() },
    body: JSON.stringify({ show_context_in_history: showContextInHistory }),
  });
  if (!response.ok) {
    throw new Error(`Create session failed: ${response.status}`);
  }
  return response.json();
}

export async function listSessions(): Promise<SessionSummary[]> {
  const response = await fetch(endpoint("/api/v1/sessions"), {
    cache: "no-store",
    headers: userHeaders(),
  });
  if (!response.ok) {
    throw new Error(`List sessions failed: ${response.status}`);
  }
  return response.json();
}

export async function getSession(sessionId: string): Promise<SessionResponse> {
  const response = await fetch(endpoint(`/api/v1/sessions/${sessionId}`), {
    cache: "no-store",
    headers: userHeaders(),
  });
  if (!response.ok) {
    throw new Error(`Get session failed: ${response.status}`);
  }
  return response.json();
}

export async function sendChat(
  sessionId: string,
  message: string,
  files: File[]
): Promise<ChatResponse> {
  const formData = new FormData();
  formData.append("session_id", sessionId);
  formData.append("message", message);
  files.forEach((file) => formData.append("files", file));

  const response = await fetch(endpoint("/api/v1/chat"), {
    method: "POST",
    headers: userHeaders(),
    body: formData,
  });

  if (!response.ok) {
    throw new Error(`Send chat failed: ${response.status}`);
  }

  return response.json();
}

export async function streamChat(
  sessionId: string,
  message: string,
  files: File[],
  onEvent: (event: StreamEvent) => void
): Promise<void> {
  const formData = new FormData();
  formData.append("session_id", sessionId);
  formData.append("message", message);
  files.forEach((file) => formData.append("files", file));

  const response = await fetch(endpoint("/api/v1/chat/stream"), {
    method: "POST",
    headers: userHeaders(),
    body: formData,
  });

  if (!response.ok || !response.body) {
    throw new Error(`Stream chat failed: ${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }

    buffer += decoder.decode(value, { stream: true });
    const chunks = buffer.split("\n\n");
    buffer = chunks.pop() || "";

    for (const chunk of chunks) {
      const lines = chunk
        .split("\n")
        .map((line) => line.trim())
        .filter((line) => line.startsWith("data:"));

      for (const line of lines) {
        const raw = line.slice(5).trim();
        if (!raw) {
          continue;
        }

        let event: StreamEvent;
        try {
          event = JSON.parse(raw) as StreamEvent;
        } catch {
          onEvent({ type: "error", message: "Malformed SSE payload." });
          continue;
        }
        onEvent(event);
      }
    }
  }
}
