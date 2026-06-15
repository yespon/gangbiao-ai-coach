"use client";

import { FormEvent, KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { createSession, getSession, listSessions, sendChat, streamChat } from "@/lib/api";
import { ChatHistoryItem, SessionSummary } from "@/types/chat";
import { checkAuth, logout, hasSessionHint } from "@/lib/auth";
import type { UserInfo } from "@/types/auth";
import AttachmentCard from "@/components/AttachmentCard";
import TypingIndicator from "@/components/TypingIndicator";

export default function HomePage() {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [sessionId, setSessionId] = useState("");
  const [history, setHistory] = useState<ChatHistoryItem[]>([]);
  const [message, setMessage] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [showContextInHistory, setShowContextInHistory] = useState(false);
  const [streamMode, setStreamMode] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [streamingDraft, setStreamingDraft] = useState("");
  const [pendingLabel, setPendingLabel] = useState("");
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [userInfo, setUserInfo] = useState<UserInfo | null>(null);
  const [showUserMenu, setShowUserMenu] = useState(false);
  const messageListRef = useRef<HTMLDivElement>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    // Quick synchronous check — if no session hint, redirect immediately
    if (!hasSessionHint()) {
      window.location.href = "/login";
      return;
    }
    // Async verification against backend
    checkAuth().then((user) => {
      if (!user) {
        window.location.href = "/login";
      } else {
        setUserInfo(user);
      }
    });
  }, []);

  useEffect(() => {
    void bootstrapSession();
  }, []);

  useEffect(() => {
    requestAnimationFrame(() => {
      messagesEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
    });
  }, [history, streamingDraft, pendingLabel, busy]);

  useEffect(() => {
    syncComposerHeight();
  }, [message]);

  const renderedMessages = useMemo(() => {
    const rows = [...history];
    if (streamingDraft) {
      rows.push({ role: "assistant", content: streamingDraft });
    }
    return rows;
  }, [history, streamingDraft]);

  const hasDraft = message.trim().length > 0 || files.length > 0;

  async function bootstrapSession() {
    try {
      setBusy(true);
      setError("");

      const data = await listSessions();
      setSessions(data);

      if (data.length > 0) {
        const first = await getSession(data[0].session_id);
        setSessionId(first.session_id);
        setHistory(first.history || []);
      } else {
        const created = await createSession(showContextInHistory);
        setSessionId(created.session_id);
        setHistory(created.history || []);
        const latest = await listSessions();
        setSessions(latest);
      }
    } catch (err) {
      setError(formatError(err));
    } finally {
      setBusy(false);
    }
  }

  function syncComposerHeight() {
    const textarea = textareaRef.current;
    if (!textarea) {
      return;
    }

    textarea.style.height = "0px";
    const next = Math.min(textarea.scrollHeight, 220);
    textarea.style.height = `${Math.max(next, 44)}px`;
    textarea.style.overflowY = textarea.scrollHeight > 220 ? "auto" : "hidden";
  }

  async function refreshSessions() {
    try {
      setError("");
      const data = await listSessions();
      setSessions(data);
    } catch (err) {
      setError(formatError(err));
    }
  }

  async function onCreateSession() {
    try {
      setBusy(true);
      setError("");
      const session = await createSession(showContextInHistory);
      setSessionId(session.session_id);
      setHistory(session.history || []);
      await refreshSessions();
    } catch (err) {
      setError(formatError(err));
    } finally {
      setBusy(false);
    }
  }

  async function onSelectSession(target: string) {
    try {
      setBusy(true);
      setError("");
      const session = await getSession(target);
      setSessionId(session.session_id);
      setHistory(session.history || []);
      setStreamingDraft("");
    } catch (err) {
      setError(formatError(err));
    } finally {
      setBusy(false);
    }
  }

  async function ensureSessionId(): Promise<string> {
    if (sessionId) {
      return sessionId;
    }
    const created = await createSession(showContextInHistory);
    setSessionId(created.session_id);
    setHistory(created.history || []);
    await refreshSessions();
    return created.session_id;
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      // While a reply is in flight, let Enter insert a newline (compose next
      // message) instead of submitting — sending is locked until reply ends.
      if (busy) return;
      event.preventDefault();
      if (message.trim() || files.length > 0) {
        void onSubmit(event as unknown as FormEvent);
      }
    }
  }

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    const text = message.trim();
    const sending = files;
    if (!text && sending.length === 0) {
      return;
    }

    try {
      setBusy(true);
      setError("");
      setStreamingDraft("");
      setPendingLabel(sending.length > 0 ? "正在解析附件…" : "正在思考…");
      // Optimistic clear: empty the composer immediately so the user can
      // compose the next message while the reply streams in.
      setMessage("");
      setFiles([]);
      const activeSessionId = await ensureSessionId();

      if (!streamMode) {
        const response = await sendChat(activeSessionId, text, sending);
        setHistory(response.history || []);
      } else {
        await streamChat(activeSessionId, text, sending, (evt) => {
          if (evt.type === "delta") {
            setStreamingDraft((prev) => prev + evt.delta);
          } else if (evt.type === "done") {
            setHistory(evt.history || []);
            setStreamingDraft("");
          } else if (evt.type === "error") {
            setError(evt.message);
            setStreamingDraft("");
          }
        });
      }

      await refreshSessions();
    } catch (err) {
      setError(formatError(err));
      // Restore composer on failure so the user doesn't lose their input.
      setMessage(text);
      setFiles(sending);
    } finally {
      setBusy(false);
      setPendingLabel("");
    }
  }

  return (
    <main className="app-shell">
      <div className={`shell ${sidebarCollapsed ? "sidebar-collapsed" : ""}`}>
        <aside className="sidebar" aria-label="会话侧边栏">
          <div className="sidebar-head">
            <h2>会话</h2>
            <button
              className={`secondary toggle-sidebar ${sidebarCollapsed ? "is-collapsed" : ""}`}
              type="button"
              onClick={() => setSidebarCollapsed((prev) => !prev)}
              aria-label={sidebarCollapsed ? "展开侧栏" : "收起侧栏"}
            >
              <svg className="toggle-icon" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <rect x="3.5" y="5" width="17" height="14" rx="3" stroke="currentColor" strokeWidth="1.7" />
                <path d="M9.5 6.5V17.5" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
              </svg>
            </button>
          </div>

          <button className="primary" type="button" onClick={onCreateSession} disabled={busy}>
            + 新建会话
          </button>

          {/* <label className="hint option-toggle">
            <input
              type="checkbox"
              checked={showContextInHistory}
              onChange={(e) => setShowContextInHistory(e.target.checked)}
            />
            创建时在历史中显示默认上下文
          </label> */}

          <div className="session-list" aria-label="session-list">
            {sessions.map((item) => (
              <button
                key={item.session_id}
                type="button"
                className={`session-item ${item.session_id === sessionId ? "active" : ""}`}
                onClick={() => void onSelectSession(item.session_id)}
              >
                <div className="session-preview">{item.latest_preview || "(empty)"}</div>
              </button>
          ))}
          </div>
          <div className="sidebar-user-bar">
            <div className={`user-avatar ${sidebarCollapsed ? "avatar-collapsed" : ""}`} style={{ background: userInfo ? avatarColor(userInfo.nickname || userInfo.email || "U") : "#94a3b8" }}>
              {userInfo ? (userInfo.nickname || userInfo.email || "U").charAt(0).toUpperCase() : "?"}
            </div>
            {!sidebarCollapsed && (
              <div className="user-info">
                <span className="user-email-masked">{userInfo ? (userInfo.nickname || maskEmail(userInfo.email)) : "用户"}</span>
              </div>
            )}
            {!sidebarCollapsed && (
              <button className="user-menu-trigger" type="button" onClick={() => setShowUserMenu((prev) => !prev)}>
                ⋮
              </button>
            )}
            {showUserMenu && !sidebarCollapsed && (
              <div className="user-popover">
                {userInfo?.is_admin ? (
                  <button type="button" onClick={() => { window.location.href = "/admin/whitelist"; }}>
                    白名单管理
                  </button>
                ) : null}
                <button type="button" onClick={() => { setShowUserMenu(false); logout(); }}>退出登录</button>
              </div>
            )}
          </div>
        </aside>

        <section className="chat">
          <header className="chat-head">
            <h1>岗标AI教练 Beta</h1>
            <div className="hint">当前会话: {sessionId || "未选择"}</div>
          </header>

          <div className="messages" ref={messageListRef}>
            {renderedMessages.length === 0 ? (
              <div className="empty-state">开始提问吧，支持文本与EXCEL（单sheet）附件联合问答。</div>
            ) : null}
            {renderedMessages.map((item, index) => (
              <div key={`${item.role}-${index}`} className={`msg-row ${item.role}`}>
                <div className="msg-role">{item.role === "assistant" ? "教练" : "用户"}</div>
                <div className={`msg ${item.role}`}>
                  {item.attachments && item.attachments.length > 0 ? (
                    <div className="attachment-list msg-attachments">
                      {item.attachments.map((a, i) => (
                        <AttachmentCard key={`${a.filename}-${i}`} name={a.filename} size={a.size} />
                      ))}
                    </div>
                  ) : null}
                  <MessageContent content={item.content} />
                </div>
              </div>
            ))}
            {busy && !streamingDraft ? (
              <div className="msg-row assistant">
                <div className="msg-role">教练</div>
                <div className="msg assistant">
                  <TypingIndicator label={pendingLabel || "正在思考…"} />
                </div>
              </div>
            ) : null}
            <div ref={messagesEndRef} />
          </div>

          <form className="composer" onSubmit={onSubmit}>
            {files.length > 0 ? (
              <div className="attachment-list" aria-live="polite">
                {files.map((f, idx) => (
                  <AttachmentCard
                    key={`${f.name}-${idx}`}
                    name={f.name}
                    size={f.size}
                    disabled={busy && !streamingDraft}
                    onRemove={() => setFiles((prev) => prev.filter((_, i) => i !== idx))}
                  />
                ))}
              </div>
            ) : null}

            <div className="composer-box">
              <label className="attach-btn" htmlFor="chat-file-input" aria-label="上传附件">
                +
              </label>
              <input
                id="chat-file-input"
                type="file"
                multiple
                onChange={(e) => {
                  const picked = Array.from(e.target.files || []);
                  setFiles((prev) => mergeFiles(prev, picked));
                  e.target.value = "";
                }}
                disabled={busy && !streamingDraft}
              />
              <textarea
                ref={textareaRef}
                rows={1}
                value={message}
                onChange={(e) => setMessage(e.target.value)}
                onInput={syncComposerHeight}
                onKeyDown={onKeyDown}
                placeholder="输入你的问题，支持结合EXCEL（单sheet）附件进行回答"
                disabled={busy && !streamingDraft}
              />
              <button
                className={`send-btn ${busy ? "send-btn-busy" : hasDraft ? "send-btn-ready" : "send-btn-idle"}`}
                type="submit"
                disabled={busy || !hasDraft}
                aria-label={busy ? "正在回复" : hasDraft ? "发送" : "请输入内容后发送"}
              >
                {busy ? (
                  <span className="send-stop" />
                ) : (
                  <svg className="send-arrow" viewBox="0 0 24 24" aria-hidden="true">
                    <path d="M12 19V7" />
                    <path d="M5.5 13.5 12 7l6.5 6.5" />
                  </svg>
                )}
              </button>
            </div>

            <div className="composer-tips">
              按 <kbd>Enter</kbd> 发送&nbsp;&nbsp;·&nbsp;&nbsp;<kbd>Shift</kbd> + <kbd>Enter</kbd> 换行
              {/* <span className="composer-tips-sep">|</span>
              <label className="option-toggle">
                <input
                  type="checkbox"
                  checked={streamMode}
                  onChange={(e) => setStreamMode(e.target.checked)}
                />
                使用流式回复
              </label> */}
            </div>
          </form>

          {error ? <div className="hint error-text">Error: {error}</div> : null}
        </section>
      </div>
    </main>
  );
}

function MessageContent({ content }: { content: string }) {
  return (
    <div className="markdown-content">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ node, ...props }) => <a {...props} target="_blank" rel="noreferrer noopener" />,
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

function mergeFiles(prev: File[], picked: File[]): File[] {
  const seen = new Set(prev.map((f) => f.name));
  const merged = [...prev];
  for (const f of picked) {
    if (!seen.has(f.name)) {
      merged.push(f);
      seen.add(f.name);
    }
  }
  return merged;
}

function formatError(err: unknown): string {
  if (err instanceof Error) {
    return err.message;
  }
  return "Unknown error";
}

function maskEmail(email: string | null): string {
  if (!email) return "用户";
  const [local, domain] = email.split("@");
  if (!domain) return email;
  return `${local.charAt(0)}***@${domain}`;
}

const AVATAR_COLORS = ["#0ea5e9", "#8b5cf6", "#ec4899", "#f59e0b", "#10b981", "#ef4444", "#6366f1", "#14b8a6"];

function avatarColor(email: string): string {
  let hash = 0;
  for (let i = 0; i < email.length; i++) {
    hash = email.charCodeAt(i) + ((hash << 5) - hash);
  }
  return AVATAR_COLORS[Math.abs(hash) % AVATAR_COLORS.length];
}
