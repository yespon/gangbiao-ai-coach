"use client";

import { useEffect, useMemo, useState } from "react";
import { getConversationSession, listConversationSessions, listConversationUsers, summarizeConversation } from "@/lib/admin";
import { checkAuth } from "@/lib/auth";
import type { AdminConversationDetail, AdminSessionSummary, ConversationUserSummary } from "@/types/admin";
import type { UserInfo } from "@/types/auth";
import type { AttachmentMeta, ChatHistoryItem } from "@/types/chat";

type ConversationScope = "mine" | "all";

export default function AdminConversationsPage() {
  const [currentUser, setCurrentUser] = useState<UserInfo | null>(null);
  const [scope, setScope] = useState<ConversationScope>("all");
  const [users, setUsers] = useState<ConversationUserSummary[]>([]);
  const [selectedUser, setSelectedUser] = useState<ConversationUserSummary | null>(null);
  const [sessions, setSessions] = useState<AdminSessionSummary[]>([]);
  const [selectedSession, setSelectedSession] = useState<AdminSessionSummary | null>(null);
  const [detail, setDetail] = useState<AdminConversationDetail | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [loadingUsers, setLoadingUsers] = useState(true);
  const [loadingSessions, setLoadingSessions] = useState(false);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [error, setError] = useState("");
  const [summary, setSummary] = useState<{ summary: string; sampled_count: number; total_count: number } | null>(null);
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [summaryError, setSummaryError] = useState("");
  const [summaryCollapsed, setSummaryCollapsed] = useState(false);

  useEffect(() => {
    let active = true;
    async function initialize() {
      setLoadingUsers(true);
      setError("");
      try {
        const user = await checkAuth();
        if (!active) return;
        const defaultScope: ConversationScope = user?.is_coach ? "mine" : "all";
        setCurrentUser(user);
        setScope(defaultScope);
        const conversationUsers = await listConversationUsers(defaultScope);
        if (!active) return;
        setUsers(conversationUsers);
      } catch (err) {
        if (active) setError(formatError(err));
      } finally {
        if (active) setLoadingUsers(false);
      }
    }
    void initialize();
    return () => {
      active = false;
    };
  }, []);

  const stats = useMemo(() => {
    const totalSessions = users.reduce((sum, user) => sum + user.session_count, 0);
    const activeUsers = users.filter((user) => user.session_count > 0).length;
    const avg = users.length === 0 ? 0 : totalSessions / users.length;
    return {
      totalSessions,
      activeUsers,
      average: avg.toFixed(1),
    };
  }, [users]);

  const sessionTitle = selectedUser ? `${formatUserName(selectedUser)} · 会话回放` : "会话回放";

  async function changeScope(nextScope: ConversationScope) {
    if (nextScope === scope) return;
    setScope(nextScope);
    setSelectedUser(null);
    setSessions([]);
    setSelectedSession(null);
    setDetail(null);
    await refreshUsers(nextScope);
  }

  async function refreshUsers(nextScope = scope) {
    setLoadingUsers(true);
    setError("");
    try {
      const conversationUsers = await listConversationUsers(nextScope);
      setUsers(conversationUsers);
    } catch (err) {
      setError(formatError(err));
    } finally {
      setLoadingUsers(false);
    }
  }

  async function openUserDialog(user: ConversationUserSummary) {
    setSelectedUser(user);
    setSelectedSession(null);
    setDetail(null);
    setSessions([]);
    setDialogOpen(true);
    setLoadingSessions(true);
    setError("");
    try {
      const userSessions = await listConversationSessions(user.managed_user_id);
      setSessions(userSessions);
      if (userSessions.length > 0) {
        await selectSession(userSessions[0]);
      }
    } catch (err) {
      setError(formatError(err));
    } finally {
      setLoadingSessions(false);
    }
  }

  function closeDialog() {
    setDialogOpen(false);
    setSelectedUser(null);
    setSelectedSession(null);
    setSessions([]);
    setDetail(null);
  }

  async function selectSession(session: AdminSessionSummary) {
    setSelectedSession(session);
    setDetail(null);
    setSummary(null);
    setSummaryError("");
    setSummaryCollapsed(false);
    setLoadingDetail(true);
    setError("");
    try {
      const conversationDetail = await getConversationSession(session.session_id);
      setDetail(conversationDetail);
    } catch (err) {
      setError(formatError(err));
    } finally {
      setLoadingDetail(false);
    }
  }

  async function runSummary() {
    if (!selectedSession) return;
    setSummaryLoading(true);
    setSummaryError("");
    setSummaryCollapsed(false);
    try {
      const result = await summarizeConversation(selectedSession.session_id);
      setSummary(result);
    } catch (err) {
      setSummaryError(formatError(err));
    } finally {
      setSummaryLoading(false);
    }
  }

  return (
    <div className="admin-page-stack">
      <section className="admin-page-header admin-conversation-header">
        <div>
          <p className="admin-kicker">Conversation Archive</p>
          <h2>对话历史</h2>
          <p>按角色权限查看历史会话。管理员可切换全部/我的学员，教练仅查看负责学员。</p>
        </div>
        <div className="admin-segmented" role="group" aria-label="对话范围">
          <button className={scope === "mine" ? "active" : ""} type="button" onClick={() => void changeScope("mine")} disabled={loadingUsers}>
            我的学员
          </button>
          {currentUser?.is_admin ? (
            <button className={scope === "all" ? "active" : ""} type="button" onClick={() => void changeScope("all")} disabled={loadingUsers}>
              全部
            </button>
          ) : null}
        </div>
      </section>

      <section className="admin-stat-grid" aria-label="会话统计">
        <article className="admin-card admin-stat-card">
          <p className="admin-kicker">Total Sessions</p>
          <h3>{stats.totalSessions}</h3>
          <p>总会话次数</p>
        </article>
        <article className="admin-card admin-stat-card">
          <p className="admin-kicker">Active Students</p>
          <h3>{stats.activeUsers}</h3>
          <p>活跃学员数</p>
        </article>
        <article className="admin-card admin-stat-card">
          <p className="admin-kicker">Average</p>
          <h3>{stats.average}</h3>
          <p>平均会话次数</p>
        </article>
      </section>

      {error ? <div className="admin-error">{error}</div> : null}

      <section className="admin-card admin-table-card">
        <div className="admin-section-head">
          <div>
            <p className="admin-kicker">Students</p>
            <h3>学员会话列表</h3>
          </div>
          <span className="admin-count">{users.length} 人</span>
        </div>

        {loadingUsers ? (
          <div className="admin-empty-state">正在加载学员会话数据...</div>
        ) : users.length === 0 ? (
          <div className="admin-empty-state">当前范围暂无学员会话。</div>
        ) : (
          <div className="admin-table-wrap">
            <table className="admin-table admin-conversation-users-table">
              <thead>
                <tr>
                  <th>姓名</th>
                  <th>工号</th>
                  <th>一级部门</th>
                  <th>所属教练</th>
                  <th>会话数</th>
                  <th>最近活跃</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {users.map((user) => (
                  <tr key={user.managed_user_id}>
                    <td>{user.name || "未填写"}</td>
                    <td>{user.employee_no}</td>
                    <td>{user.department_level1 || "未填写"}</td>
                    <td>{user.coach_name || "未分配"}</td>
                    <td>{user.session_count}</td>
                    <td>{formatDate(user.latest_session_at)}</td>
                    <td>
                      <button className="admin-link-button" type="button" onClick={() => void openUserDialog(user)}>
                        查看
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {dialogOpen ? (
        <div className="admin-dialog-backdrop" role="presentation" onClick={closeDialog}>
          <section className="admin-dialog admin-conversation-dialog" role="dialog" aria-modal="true" aria-label="会话详情" onClick={(event) => event.stopPropagation()}>
            <div className="admin-dialog-head">
              <div>
                <p className="admin-kicker">Conversation Playback</p>
                <h3>{sessionTitle}</h3>
                <p>{selectedUser ? `共 ${sessions.length} 条记录` : "请选择学员"}</p>
              </div>
              <div className="admin-dialog-head-actions">
                <button
                  className="admin-summary-btn"
                  type="button"
                  onClick={() => void runSummary()}
                  disabled={!selectedSession || summaryLoading}
                  aria-label="AI 速览"
                >
                  ✨ AI 速览
                </button>
                <button className="admin-dialog-close" type="button" onClick={closeDialog} aria-label="关闭">
                  ×
                </button>
              </div>
            </div>

            <div className="admin-conversation-dialog-grid">
              <aside className="admin-session-pane" aria-label="会话列表">
                {loadingSessions ? (
                  <div className="admin-empty-state">正在加载会话列表...</div>
                ) : sessions.length === 0 ? (
                  <div className="admin-empty-state">该学员暂无会话。</div>
                ) : (
                  <div className="admin-session-list">
                    {sessions.map((session) => {
                      const selected = selectedSession?.session_id === session.session_id;
                      return (
                        <button
                          className={`admin-session-item ${selected ? "active" : ""}`}
                          type="button"
                          key={session.session_id}
                          onClick={() => void selectSession(session)}
                        >
                          <strong>{session.latest_preview || "暂无内容"}</strong>
                          <span>{formatDate(session.updated_at)}</span>
                        </button>
                      );
                    })}
                  </div>
                )}
              </aside>

              <section className="admin-conversation-pane">
                {!selectedSession ? (
                  <div className="admin-empty-state">请选择会话查看详情。</div>
                ) : (
                  <>
                    <ConversationSummarySection
                      summary={summary}
                      loading={summaryLoading}
                      error={summaryError}
                      collapsed={summaryCollapsed}
                      onToggle={() => setSummaryCollapsed((prev) => !prev)}
                      onRetry={() => void runSummary()}
                    />
                    {loadingDetail ? (
                      <div className="admin-empty-state">正在加载会话详情...</div>
                    ) : detail ? (
                      <div className="admin-detail-stack">
                        <div className="admin-detail-meta">
                          <span>{formatUserName(detail.student)}</span>
                          <span>创建：{formatDate(detail.created_at)}</span>
                          <span>更新：{formatDate(detail.updated_at)}</span>
                        </div>
                        <div className="admin-message-list" aria-label="会话消息">
                          {detail.history.length === 0 ? (
                            <div className="admin-empty-state">该会话暂无消息内容。</div>
                          ) : (
                            detail.history.map((message, index) => (
                              <ConversationMessage message={message} index={index} key={`${message.role}-${index}`} />
                            ))
                          )}
                        </div>
                      </div>
                    ) : (
                      <div className="admin-empty-state">暂无详情。</div>
                    )}
                  </>
                )}
              </section>
            </div>
          </section>
        </div>
      ) : null}
    </div>
  );
}

function ConversationMessage({ message, index }: { message: ChatHistoryItem; index: number }) {
  const isAssistant = message.role === "assistant";
  return (
    <article className={`admin-message ${isAssistant ? "admin-message-assistant" : "admin-message-user"}`}>
      <div className="admin-message-head">
        <span>{isAssistant ? "AI 教练" : "学员"}</span>
        <time>{message.created_at ? formatDate(message.created_at) : `#${index + 1}`}</time>
      </div>
      <div className="admin-message-content">{message.content || "（空消息）"}</div>
      {message.attachments && message.attachments.length > 0 ? <AttachmentList attachments={message.attachments} /> : null}
    </article>
  );
}

function AttachmentList({ attachments }: { attachments: AttachmentMeta[] }) {
  return (
    <div className="admin-attachment-list" aria-label="附件列表">
      {attachments.map((attachment, index) => (
        <div className="admin-attachment-chip" key={`${attachment.filename}-${index}`}>
          <span>{attachment.filename}</span>
          <small>{formatSize(attachment.size)}</small>
        </div>
      ))}
    </div>
  );
}

function formatUserName(user: Pick<ConversationUserSummary, "employee_no" | "name" | "department_level1"> | null) {
  if (!user) return "未选择学员";
  const name = user.name || user.employee_no;
  return user.department_level1 ? `${name} · ${user.department_level1}` : name;
}

function formatDate(value: string | null | undefined) {
  if (!value) return "未记录";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN");
}

function formatSize(size: number | undefined) {
  if (!size) return "大小未知";
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function formatError(err: unknown) {
  return err instanceof Error ? err.message : "请求失败";
}

function ConversationSummarySection({
  summary,
  loading,
  error,
  collapsed,
  onToggle,
  onRetry,
}: {
  summary: { summary: string; sampled_count: number; total_count: number } | null;
  loading: boolean;
  error: string;
  collapsed: boolean;
  onToggle: () => void;
  onRetry: () => void;
}) {
  if (!summary && !loading && !error) {
    return (
      <div className="admin-empty-state" style={{ minHeight: 80 }}>
        点击右上角「AI 速览」生成此会话摘要。
      </div>
    );
  }
  return (
    <div className="admin-summary-panel">
      <div className="admin-summary-panel-head">
        <span>AI 速览</span>
        <div className="admin-summary-panel-actions">
          {summary ? (
            <button className="admin-link-button" type="button" onClick={onToggle}>
              {collapsed ? "展开" : "收起"}
            </button>
          ) : null}
          <button className="admin-link-button" type="button" onClick={onRetry} disabled={loading}>
            重新生成
          </button>
        </div>
      </div>
      {error ? <div className="admin-error">{error}</div> : null}
      {loading ? (
        <div className="admin-summary-panel-meta">正在生成速览...</div>
      ) : summary ? (
        <>
          {!collapsed ? (
            <div className="admin-summary-panel-body">{summary.summary}</div>
          ) : null}
          <div className="admin-summary-panel-meta">
            基于 {summary.sampled_count} / {summary.total_count} 条消息
          </div>
        </>
      ) : null}
    </div>
  );
}
