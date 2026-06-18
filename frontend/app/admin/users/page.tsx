"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import AdminPagination from "@/components/admin/AdminPagination";
import {
  createManagedUser,
  importManagedUsers,
  listCoachOptions,
  listManagedUsers,
  managedUsersTemplateUrl,
  updateManagedUser,
} from "@/lib/admin";
import type {
  CoachOption,
  ImportResult,
  ManagedUser,
  ManagedUserFilters,
  ManagedUserPayload,
  ManagedUserRole,
} from "@/types/admin";

const roleLabels: Record<ManagedUserRole, string> = {
  admin: "管理员",
  coach: "教练",
  student: "学员",
};

const emptyForm: ManagedUserPayload = {
  employee_no: "",
  name: "",
  email: "",
  department_level1: "",
  primary_role: "student",
  is_coach: false,
  coach_id: null,
  enabled: true,
};

const PAGE_SIZE_STORAGE = "admin.users.pageSize";
const PAGE_SIZE_OPTIONS = [10, 30, 50, 100] as const;

type RoleFilter = "all" | "admin" | "coach" | "student";
type EnabledFilter = "all" | "yes" | "no";
type EmailFilter = "all" | "yes" | "no";

function readPageSize(): number {
  if (typeof window === "undefined") return 30;
  const raw = window.localStorage.getItem(PAGE_SIZE_STORAGE);
  if (!raw) return 30;
  const n = Number(raw);
  return PAGE_SIZE_OPTIONS.includes(n as (typeof PAGE_SIZE_OPTIONS)[number]) ? n : 30;
}

export default function AdminUsersPage() {
  const [users, setUsers] = useState<ManagedUser[]>([]);
  const [coaches, setCoaches] = useState<CoachOption[]>([]);
  const [total, setTotal] = useState(0);
  const [form, setForm] = useState<ManagedUserPayload>(emptyForm);
  const [editingUser, setEditingUser] = useState<ManagedUser | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [importResult, setImportResult] = useState<ImportResult | null>(null);

  // Filters
  const [q, setQ] = useState("");
  const [roleFilter, setRoleFilter] = useState<RoleFilter>("all");
  const [enabledFilter, setEnabledFilter] = useState<EnabledFilter>("all");
  const [coachFilter, setCoachFilter] = useState<"all" | "unassigned" | string>("all");
  const [department, setDepartment] = useState("");
  const [emailFilter, setEmailFilter] = useState<EmailFilter>("all");

  // Pagination
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState<number>(30);

  useEffect(() => {
    setPageSize(readPageSize());
  }, []);

  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, roleFilter, enabledFilter, coachFilter, department, emailFilter, page, pageSize]);

  useEffect(() => {
    void listCoachOptions().then(setCoaches).catch(() => undefined);
  }, []);

  const coachChoices = useMemo(() => {
    if (!editingUser) return coaches;
    return coaches.filter((coach) => coach.id !== editingUser.id);
  }, [coaches, editingUser]);

  const stats = useMemo(() => {
    const admins = users.filter((user) => user.primary_role === "admin").length;
    const coachesCount = users.filter(
      (user) => user.primary_role === "coach" || user.is_coach,
    ).length;
    const students = users.filter((user) => user.primary_role === "student").length;
    return { total: users.length, admins, coaches: coachesCount, students };
  }, [users]);

  function buildFilters(): ManagedUserFilters {
    return {
      q: q.trim() || null,
      role: roleFilter === "all" ? null : roleFilter,
      enabled: enabledFilter === "all" ? null : enabledFilter === "yes",
      coach_filter: coachFilter,
      department_level1: department.trim() || null,
      has_email: emailFilter === "all" ? null : emailFilter === "yes",
    };
  }

  async function refresh() {
    setLoading(true);
    setError("");
    try {
      const result = await listManagedUsers(buildFilters(), page, pageSize);
      setUsers(result.items);
      setTotal(result.total);
    } catch (err) {
      setError(formatError(err));
    } finally {
      setLoading(false);
    }
  }

  function resetFilters() {
    setQ("");
    setRoleFilter("all");
    setEnabledFilter("all");
    setCoachFilter("all");
    setDepartment("");
    setEmailFilter("all");
    setPage(1);
  }

  function changePage(next: number) {
    setPage(next);
  }

  function changePageSize(next: number) {
    setPageSize(next);
    setPage(1);
  }

  function openCreateDialog() {
    setEditingUser(null);
    setForm(emptyForm);
    setDialogOpen(true);
    setImportResult(null);
    setNotice("");
    setError("");
  }

  function openEditDialog(user: ManagedUser) {
    setEditingUser(user);
    setForm({
      employee_no: user.employee_no,
      name: user.name || "",
      email: user.email || "",
      department_level1: user.department_level1 || "",
      primary_role: user.primary_role,
      is_coach: user.is_coach,
      coach_id: user.coach_id,
      enabled: user.enabled,
    });
    setDialogOpen(true);
    setImportResult(null);
    setNotice("");
    setError("");
  }

  function closeDialog() {
    if (busy) return;
    setDialogOpen(false);
    setEditingUser(null);
    setForm(emptyForm);
  }

  function updateForm<K extends keyof ManagedUserPayload>(key: K, value: ManagedUserPayload[K]) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const payload = normalizePayload(form);
      if (editingUser) {
        await updateManagedUser(editingUser.id, payload);
        setNotice("用户信息已更新");
      } else {
        await createManagedUser(payload);
        setNotice("用户已创建");
      }
      setDialogOpen(false);
      setEditingUser(null);
      setForm(emptyForm);
      await refresh();
    } catch (err) {
      setError(formatError(err));
    } finally {
      setBusy(false);
    }
  }

  async function onImport(file: File | null) {
    if (!file) return;
    setBusy(true);
    setError("");
    setNotice("");
    setImportResult(null);
    try {
      const result = await importManagedUsers(file);
      setImportResult(result);
      setNotice("批量导入已完成");
      await refresh();
    } catch (err) {
      setError(formatError(err));
    } finally {
      setBusy(false);
    }
  }

  async function toggleEnabled(user: ManagedUser, enabled: boolean) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await updateManagedUser(user.id, { enabled });
      await refresh();
    } catch (err) {
      setError(formatError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="admin-page-stack">
      <section className="admin-page-header">
        <div>
          <p className="admin-kicker">Managed Users</p>
          <h2>用户管理</h2>
          <p>统一维护账号角色、教练归属和可用状态，确保学员与教练关系清晰。</p>
        </div>
        <div className="admin-actions-row">
          <a className="admin-button admin-button-muted" href={managedUsersTemplateUrl()}>
            下载批量导入模板
          </a>
          <label className={`admin-button admin-button-muted ${busy ? "disabled" : ""}`}>
            上传并导入
            <input
              type="file"
              accept=".xlsx"
              hidden
              disabled={busy}
              onChange={(event) => void onImport(event.target.files?.[0] || null)}
            />
          </label>
          <button className="admin-button admin-button-primary" type="button" onClick={openCreateDialog}>
            添加用户
          </button>
        </div>
      </section>

      <section className="admin-stat-grid" aria-label="用户统计">
        <article className="admin-card admin-stat-card">
          <p className="admin-kicker">Total</p>
          <h3>{total}</h3>
          <p>命中条数</p>
        </article>
        <article className="admin-card admin-stat-card">
          <p className="admin-kicker">Admin</p>
          <h3>{stats.admins}</h3>
          <p>管理员(本页)</p>
        </article>
        <article className="admin-card admin-stat-card">
          <p className="admin-kicker">Coach</p>
          <h3>{stats.coaches}</h3>
          <p>教练(本页)</p>
        </article>
        <article className="admin-card admin-stat-card">
          <p className="admin-kicker">Student</p>
          <h3>{stats.students}</h3>
          <p>学员(本页)</p>
        </article>
      </section>

      <section className="admin-user-toolbar" aria-label="过滤">
        <label>
          关键词
          <input
            value={q}
            onChange={(event) => {
              setQ(event.target.value);
              setPage(1);
            }}
            placeholder="工号 / 姓名 / 邮箱 / 部门"
          />
        </label>
        <label>
          主角色
          <select
            value={roleFilter}
            onChange={(event) => {
              setRoleFilter(event.target.value as RoleFilter);
              setPage(1);
            }}
          >
            <option value="all">全部</option>
            <option value="admin">管理员</option>
            <option value="coach">教练</option>
            <option value="student">学员</option>
          </select>
        </label>
        <label>
          启用状态
          <select
            value={enabledFilter}
            onChange={(event) => {
              setEnabledFilter(event.target.value as EnabledFilter);
              setPage(1);
            }}
          >
            <option value="all">全部</option>
            <option value="yes">已启用</option>
            <option value="no">已禁用</option>
          </select>
        </label>
        <label>
          所属教练
          <select
            value={coachFilter}
            onChange={(event) => {
              setCoachFilter(event.target.value);
              setPage(1);
            }}
          >
            <option value="all">全部</option>
            <option value="unassigned">未分配</option>
            {coaches.map((coach) => (
              <option key={coach.id} value={coach.id}>
                {coach.name || coach.employee_no}
                {coach.department_level1 ? ` · ${coach.department_level1}` : ""}
              </option>
            ))}
          </select>
        </label>
        <label>
          一级部门
          <input
            value={department}
            onChange={(event) => {
              setDepartment(event.target.value);
              setPage(1);
            }}
            placeholder="精确匹配"
          />
        </label>
        <label>
          邮箱
          <select
            value={emailFilter}
            onChange={(event) => {
              setEmailFilter(event.target.value as EmailFilter);
              setPage(1);
            }}
          >
            <option value="all">全部</option>
            <option value="yes">有邮箱</option>
            <option value="no">无邮箱</option>
          </select>
        </label>
        <div className="admin-user-toolbar-spacer" />
        <button
          type="button"
          className="admin-button admin-button-muted"
          onClick={resetFilters}
        >
          重置
        </button>
      </section>

      {importResult ? (
        <section className="admin-result-panel">
          <strong>导入完成</strong>
          <span>新增 {importResult.created}</span>
          <span>更新 {importResult.updated}</span>
          <span>跳过 {importResult.skipped}</span>
          {importResult.errors.length > 0 ? (
            <details>
              <summary>查看错误 {importResult.errors.length} 条</summary>
              <ul>
                {importResult.errors.map((item) => (
                  <li key={`${item.row}-${item.reason}`}>第 {item.row} 行：{item.reason}</li>
                ))}
              </ul>
            </details>
          ) : null}
        </section>
      ) : null}

      {notice ? <div className="admin-notice">{notice}</div> : null}
      {error ? <div className="admin-error">{error}</div> : null}

      <section className="admin-card admin-table-card">
        <div className="admin-section-head">
          <div>
            <p className="admin-kicker">Directory</p>
            <h3>用户列表</h3>
          </div>
          <span className="admin-count">{users.length} 人</span>
        </div>

        {loading ? (
          <div className="admin-empty-state">正在加载用户数据...</div>
        ) : users.length === 0 ? (
          <div className="admin-empty-state">没有匹配的用户，试试调整过滤条件。</div>
        ) : (
          <div className="admin-table-wrap">
            <table className="admin-table managed-users-table">
              <thead>
                <tr>
                  <th>工号</th>
                  <th>姓名</th>
                  <th>邮箱</th>
                  <th>一级部门</th>
                  <th>主角色</th>
                  <th>兼任教练</th>
                  <th>所属教练</th>
                  <th>状态</th>
                  <th>更新时间</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {users.map((user) => (
                  <tr key={user.id}>
                    <td>{user.employee_no}</td>
                    <td>{user.name || "未填写"}</td>
                    <td>{user.email || "未填写"}</td>
                    <td>{user.department_level1 || "未填写"}</td>
                    <td>
                      <RoleBadge user={user} />
                    </td>
                    <td>{renderCoachCapability(user)}</td>
                    <td>{user.coach_name || "未指定"}</td>
                    <td>
                      <label className="admin-toggle">
                        <input
                          type="checkbox"
                          checked={user.enabled}
                          disabled={busy}
                          onChange={(event) => void toggleEnabled(user, event.target.checked)}
                        />
                        <span>{user.enabled ? "启用" : "禁用"}</span>
                      </label>
                    </td>
                    <td>{formatDate(user.updated_at)}</td>
                    <td>
                      <button
                        className="admin-link-button"
                        type="button"
                        onClick={() => openEditDialog(user)}
                        disabled={busy}
                      >
                        编辑
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <AdminPagination
          page={page}
          pageSize={pageSize}
          total={total}
          pageSizeOptions={[...PAGE_SIZE_OPTIONS]}
          onPageChange={changePage}
          onPageSizeChange={changePageSize}
          storageKey={PAGE_SIZE_STORAGE}
        />
      </section>

      {dialogOpen ? (
        <div className="admin-dialog-backdrop" role="presentation" onClick={closeDialog}>
          <section
            className="admin-dialog"
            role="dialog"
            aria-modal="true"
            aria-label={editingUser ? "编辑用户" : "添加用户"}
            onClick={(event) => event.stopPropagation()}
          >
            <div className="admin-dialog-head">
              <div>
                <p className="admin-kicker">{editingUser ? "Edit User" : "Add User"}</p>
                <h3>{editingUser ? "编辑用户信息" : "添加用户"}</h3>
                <p>
                  {editingUser
                    ? "仅支持修改角色、教练归属和状态"
                    : "录入单条用户信息；批量请使用页面顶部的「上传并导入」。"}
                </p>
              </div>
              <button className="admin-dialog-close" type="button" onClick={closeDialog} aria-label="关闭">
                ×
              </button>
            </div>

            {editingUser?.is_system_admin ? (
              <div className="admin-inline-warning">系统管理员账号不可修改工号、角色或启用状态。</div>
            ) : null}

            <form className="admin-user-form" onSubmit={onSubmit}>
              <div className="admin-form-grid">
                <label>
                  工号
                  <input
                    value={form.employee_no}
                    onChange={(event) => updateForm("employee_no", event.target.value)}
                    placeholder="请输入工号"
                    required
                    disabled={busy || Boolean(editingUser?.is_system_admin)}
                  />
                </label>
                <label>
                  姓名
                  <input
                    value={form.name || ""}
                    onChange={(event) => updateForm("name", event.target.value)}
                    placeholder="请输入姓名"
                    disabled={busy}
                  />
                </label>
                <label>
                  邮箱
                  <input
                    value={form.email || ""}
                    onChange={(event) => updateForm("email", event.target.value)}
                    placeholder="请输入邮箱"
                    type="email"
                    disabled={busy}
                  />
                </label>
                <label>
                  一级部门
                  <input
                    value={form.department_level1 || ""}
                    onChange={(event) => updateForm("department_level1", event.target.value)}
                    placeholder="请输入一级部门"
                    disabled={busy}
                  />
                </label>
                <label>
                  主角色
                  <select
                    value={form.primary_role}
                    onChange={(event) => updateForm("primary_role", event.target.value as ManagedUserRole)}
                    disabled={busy || Boolean(editingUser?.is_system_admin)}
                  >
                    {Object.entries(roleLabels).map(([value, label]) => (
                      <option key={value} value={value}>
                        {label}
                      </option>
                    ))}
                  </select>
                </label>
                {form.primary_role === "student" ? (
                  <label>
                    教练归属
                    <select
                      value={form.coach_id || ""}
                      onChange={(event) => updateForm("coach_id", event.target.value || null)}
                      disabled={busy}
                    >
                      <option value="">未指定</option>
                      {coachChoices.map((coach) => (
                        <option key={coach.id} value={coach.id}>
                          {coach.name || coach.employee_no}
                          {coach.department_level1 ? ` · ${coach.department_level1}` : ""}
                        </option>
                      ))}
                    </select>
                  </label>
                ) : null}
              </div>

              <div className="admin-switch-row">
                {form.primary_role === "admin" ? (
                  <label className="admin-switch">
                    <input
                      type="checkbox"
                      checked={form.is_coach}
                      onChange={(event) => updateForm("is_coach", event.target.checked)}
                      disabled={busy}
                    />
                    <span>管理员兼任教练</span>
                  </label>
                ) : null}
                <label className="admin-switch">
                  <input
                    type="checkbox"
                    checked={form.enabled}
                    onChange={(event) => updateForm("enabled", event.target.checked)}
                    disabled={busy || Boolean(editingUser?.is_system_admin)}
                  />
                  <span>启用账号</span>
                </label>
              </div>

              <div className="admin-dialog-actions">
                <button className="admin-button admin-button-muted" type="button" onClick={closeDialog} disabled={busy}>
                  取消
                </button>
                <button className="admin-button admin-button-primary" type="submit" disabled={busy}>
                  {editingUser ? "保存" : "添加用户"}
                </button>
              </div>
            </form>
          </section>
        </div>
      ) : null}
    </div>
  );
}

function normalizePayload(payload: ManagedUserPayload): ManagedUserPayload {
  const normalizedRole = payload.primary_role;
  const normalizedCoachId = normalizedRole === "student" ? payload.coach_id || null : null;
  return {
    employee_no: payload.employee_no.trim(),
    name: emptyToNull(payload.name),
    email: emptyToNull(payload.email),
    department_level1: emptyToNull(payload.department_level1),
    primary_role: normalizedRole,
    is_coach: normalizedRole === "coach" ? true : normalizedRole === "admin" ? payload.is_coach : false,
    coach_id: normalizedCoachId,
    enabled: payload.enabled,
  };
}

function formatDate(value: string | null | undefined) {
  if (!value) return "未记录";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN");
}

function emptyToNull(value: string | null | undefined): string | null {
  const trimmed = (value || "").trim();
  return trimmed ? trimmed : null;
}

function formatError(err: unknown) {
  return err instanceof Error ? err.message : "请求失败";
}

function RoleBadge({ user }: { user: ManagedUser }) {
  return (
    <>
      <span className="admin-pill">{roleLabels[user.primary_role]}</span>
      {user.is_system_admin ? <span className="admin-pill admin-pill-gold">系统管理员</span> : null}
    </>
  );
}

function renderCoachCapability(user: ManagedUser) {
  if (user.primary_role === "admin") {
    return user.is_coach ? (
      <span className="admin-pill admin-pill-green">是</span>
    ) : (
      <span>否</span>
    );
  }
  if (user.primary_role === "coach") {
    return <span className="admin-pill admin-pill-green">具备</span>;
  }
  return <span aria-label="不具备">—</span>;
}
