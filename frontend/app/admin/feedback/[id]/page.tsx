"use client";

import { use, useEffect, useState } from "react";
import Link from "next/link";

import { adminGetFeedback, adminPatchFeedbackStatus } from "@/lib/feedback";
import { checkAuth } from "@/lib/auth";
import type { UserInfo } from "@/types/auth";
import type { FeedbackDetail, FeedbackStatus } from "@/types/feedback";

const STATUS_LABELS: Record<FeedbackStatus, string> = {
  open: "未读",
  read: "已读",
  resolved: "已处理",
};

export default function AdminFeedbackDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [user, setUser] = useState<UserInfo | null>(null);
  const [detail, setDetail] = useState<FeedbackDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    void checkAuth().then(setUser);
  }, []);

  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  async function refresh() {
    setLoading(true);
    setError("");
    try {
      const data = await adminGetFeedback(id);
      setDetail(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }

  async function transitionStatus(target: FeedbackStatus) {
    setBusy(true);
    setError("");
    try {
      await adminPatchFeedbackStatus(id, target);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "操作失败");
    } finally {
      setBusy(false);
    }
  }

  if (user && !user.is_admin) {
    return <div className="admin-error">仅管理员可访问。</div>;
  }

  if (loading) {
    return <div className="admin-empty-state">正在加载反馈详情…</div>;
  }

  if (!detail) {
    return <div className="admin-error">{error || "未找到该反馈。"}</div>;
  }

  const submitter = detail.submitter;

  return (
    <div className="admin-page-stack">
      <section className="admin-page-header">
        <div>
          <p className="admin-kicker">Feedback Detail</p>
          <h2>反馈详情</h2>
          <p>查看完整内容、提交人信息与附件,根据需要切换状态。</p>
        </div>
        <Link className="admin-button admin-button-muted" href="/admin/feedback">
          返回列表
        </Link>
      </section>

      {error ? <div className="admin-error">{error}</div> : null}

      <section className="admin-card">
        <div className="admin-detail-meta" style={{ marginBottom: 8 }}>
          <span>提交时间: {formatDate(detail.created_at)}</span>
          <span>状态: {STATUS_LABELS[detail.status]}</span>
          {detail.read_at ? <span>已读: {formatDate(detail.read_at)}</span> : null}
          {detail.resolved_at ? <span>已处理: {formatDate(detail.resolved_at)}</span> : null}
        </div>
        <div className="admin-detail-meta" style={{ marginBottom: 16 }}>
          <span>IP: {detail.ip || "未记录"}</span>
          <span>UA: {detail.user_agent || "未记录"}</span>
        </div>
        <div className="admin-card" style={{ background: "rgba(241,245,249,0.6)" }}>
          <p className="admin-kicker">Submitter</p>
          <p>
            {submitter.name || "未填写"} ({submitter.employee_no || "无工号"})
            <br />
            {submitter.email || "—"}
            {submitter.department_level1 ? ` · ${submitter.department_level1}` : ""}
            {submitter.primary_role ? ` · ${submitter.primary_role}` : ""}
          </p>
        </div>
      </section>

      <section className="admin-card">
        <p className="admin-kicker">Content</p>
        <pre className="admin-feedback-content">{detail.content}</pre>
      </section>

      {detail.attachments.length > 0 ? (
        <section className="admin-card">
          <p className="admin-kicker">Attachments</p>
          <div className="feedback-dialog-thumbnails">
            {detail.attachments.map((att) => (
              <a key={att.id} href={att.url} target="_blank" rel="noreferrer" className="feedback-dialog-thumb">
                <img src={att.url} alt={att.filename} />
              </a>
            ))}
          </div>
        </section>
      ) : null}

      <section className="admin-card">
        <div className="admin-dialog-actions">
          {detail.status === "open" ? (
            <button
              className="admin-button admin-button-muted"
              type="button"
              onClick={() => void transitionStatus("read")}
              disabled={busy}
            >
              标记为已读
            </button>
          ) : null}
          {detail.status !== "resolved" ? (
            <button
              className="admin-button admin-button-primary"
              type="button"
              onClick={() => void transitionStatus("resolved")}
              disabled={busy}
            >
              标记为已处理
            </button>
          ) : null}
        </div>
      </section>
    </div>
  );
}

function formatDate(value: string | null | undefined) {
  if (!value) return "未记录";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN");
}
