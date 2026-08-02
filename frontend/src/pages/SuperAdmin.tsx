import { useState, useEffect } from "react";
import { useTranslation } from "react-i18next";
import { NavBar } from "../components/NavBar";
import { useAuth } from "../context/AuthContext";
import api from "../api/client";

// ── types ─────────────────────────────────────────────────────────────────────

type WorkspaceAdminView = {
  id: string; name: string; owner_user_id: string; owner_email: string | null;
  payment_enabled: boolean; subscription_status: string; plan_name: string | null;
  price_usd_monthly: string | null; period_spend_usd: string;
  monthly_budget_cap: string | null; member_count: number; created_at: string;
};
type ProfitabilityView = {
  total_revenue_usd: string; total_compute_cost_usd: string;
  realized_margin_usd: string; margin_pct: number | null;
  workspace_count: number; active_subscriptions: number;
  at_risk_workspaces: { workspace_id: string; spend_usd: string; included_budget_usd: string; pct: number | null }[];
};
type AuditEntry = {
  id: string; admin_user_id: string; action: string;
  target_workspace_id: string | null; target_user_id: string | null;
  details: Record<string, unknown>; created_at: string;
};

// ── style helpers ─────────────────────────────────────────────────────────────

const card: React.CSSProperties = {
  background: "#fff", border: "1px solid #e0e0e0", borderRadius: "8px",
  padding: "1.25rem 1.5rem", marginBottom: "1.25rem",
};
const sectionTitle: React.CSSProperties = {
  margin: "0 0 0.85rem", fontSize: "0.85rem", fontWeight: 700,
  color: "#555", textTransform: "uppercase", letterSpacing: "0.04em",
};
const badge = (color: string): React.CSSProperties => ({
  display: "inline-block", fontSize: "0.71rem", padding: "2px 8px",
  borderRadius: "10px", background: color + "22", color, fontWeight: 600,
  border: `1px solid ${color}44`,
});
const STATUS_COLORS: Record<string, string> = {
  active: "#27ae60", past_due: "#e67e22", canceled: "#c0392b", inactive: "#aaa",
};
const inp: React.CSSProperties = {
  padding: "0.4rem 0.65rem", border: "1px solid #ddd", borderRadius: "4px",
  fontSize: "0.875rem", width: "100%", boxSizing: "border-box",
};
const btn = (bg: string, color = "#fff"): React.CSSProperties => ({
  padding: "0.4rem 1rem", background: bg, color, border: "none",
  borderRadius: "4px", cursor: "pointer", fontWeight: 600, fontSize: "0.82rem",
});

// ── Create workspace panel ────────────────────────────────────────────────────

function CreateWorkspacePanel({ t, onCreated }: { t: (k: string) => string; onCreated: () => void }) {
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const create = async () => {
    if (!email.trim() || !name.trim()) return;
    setBusy(true); setError("");
    try {
      await api.post("/admin/workspaces", { owner_email: email, name });
      setEmail(""); setName("");
      onCreated();
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setError(msg || t("common.error"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap", alignItems: "flex-end" }}>
      <div style={{ flex: 1, minWidth: "180px" }}>
        <label style={{ fontSize: "0.78rem", color: "#555", display: "block", marginBottom: "0.2rem" }}>{t("admin.ownerEmail")}</label>
        <input style={inp} value={email} onChange={(e) => setEmail(e.target.value)} placeholder="user@example.com" />
      </div>
      <div style={{ flex: 1, minWidth: "140px" }}>
        <label style={{ fontSize: "0.78rem", color: "#555", display: "block", marginBottom: "0.2rem" }}>{t("admin.workspaceName")}</label>
        <input style={inp} value={name} onChange={(e) => setName(e.target.value)} placeholder="Workspace name" />
      </div>
      <button onClick={create} disabled={busy} style={{ ...btn("#1a1a2e"), opacity: busy ? 0.65 : 1 }}>
        {busy ? t("admin.creating") : t("admin.create")}
      </button>
      {error && <span style={{ fontSize: "0.78rem", color: "#c0392b", width: "100%" }}>{error}</span>}
    </div>
  );
}

// ── Workspace row ─────────────────────────────────────────────────────────────

function WorkspaceRow({ ws, t, onToggled }: {
  ws: WorkspaceAdminView;
  t: (k: string) => string;
  onToggled: (id: string, enabled: boolean) => void;
}) {
  const [busy, setBusy] = useState(false);

  const togglePayment = async () => {
    setBusy(true);
    try {
      await api.patch(`/admin/workspaces/${ws.id}/payment`, { payment_enabled: !ws.payment_enabled });
      onToggled(ws.id, !ws.payment_enabled);
    } finally {
      setBusy(false);
    }
  };

  const statusColor = STATUS_COLORS[ws.subscription_status] || "#aaa";
  const spend = parseFloat(ws.period_spend_usd || "0");
  const cap = ws.monthly_budget_cap ? parseFloat(ws.monthly_budget_cap) : null;
  const pct = cap && cap > 0 ? spend / cap : null;
  const pctColor = pct !== null ? (pct >= 1 ? "#c0392b" : pct >= 0.8 ? "#e67e22" : "#27ae60") : "#aaa";

  return (
    <tr style={{ borderBottom: "1px solid #f4f4f4", fontSize: "0.82rem" }}>
      <td style={{ padding: "0.5rem 0.5rem 0.5rem 0" }}>
        <div style={{ fontWeight: 600, color: "#1a1a2e" }}>{ws.name}</div>
        <div style={{ color: "#aaa", fontSize: "0.72rem" }}>{ws.owner_email}</div>
      </td>
      <td style={{ padding: "0.5rem" }}>
        <span style={badge(statusColor)}>{t(`billing.${ws.subscription_status}`)}</span>
        {ws.plan_name && <span style={{ fontSize: "0.7rem", color: "#888", marginLeft: "0.3rem" }}>{ws.plan_name}</span>}
      </td>
      <td style={{ padding: "0.5rem", textAlign: "right", color: "#333" }}>
        <span style={{ color: pctColor }}>${spend.toFixed(2)}</span>
        {cap && <span style={{ color: "#aaa" }}> / ${cap.toFixed(2)}</span>}
      </td>
      <td style={{ padding: "0.5rem", textAlign: "center", color: "#666" }}>{ws.member_count}</td>
      <td style={{ padding: "0.5rem" }}>
        <span style={badge(ws.payment_enabled ? "#1565c0" : "#aaa")}>
          {ws.payment_enabled ? t("admin.paymentEnabled") : t("admin.paymentDisabled")}
        </span>
      </td>
      <td style={{ padding: "0.5rem" }}>
        <button
          onClick={togglePayment}
          disabled={busy}
          style={{ ...btn(ws.payment_enabled ? "#c0392b11" : "#27ae6011", ws.payment_enabled ? "#c0392b" : "#27ae60"), border: `1px solid ${ws.payment_enabled ? "#c0392b33" : "#27ae6033"}`, opacity: busy ? 0.6 : 1 }}
        >
          {ws.payment_enabled ? t("admin.disable") : t("admin.enable")}
        </button>
      </td>
    </tr>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

type TabId = "workspaces" | "profitability" | "audit";

export function SuperAdmin() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const [tab, setTab] = useState<TabId>("workspaces");
  const [workspaces, setWorkspaces] = useState<WorkspaceAdminView[]>([]);
  const [profitability, setProfitability] = useState<ProfitabilityView | null>(null);
  const [auditLog, setAuditLog] = useState<AuditEntry[]>([]);
  const [loading, setLoading] = useState(true);

  const isSuperAdmin = user?.platform_role === "super_admin";

  const loadWorkspaces = async () => {
    const res = await api.get<WorkspaceAdminView[]>("/admin/workspaces");
    setWorkspaces(res.data);
  };
  const loadProfitability = async () => {
    const res = await api.get<ProfitabilityView>("/admin/profitability");
    setProfitability(res.data);
  };
  const loadAudit = async () => {
    const res = await api.get<AuditEntry[]>("/admin/audit-log");
    setAuditLog(res.data);
  };

  useEffect(() => {
    if (!isSuperAdmin) return;
    (async () => {
      setLoading(true);
      try {
        await Promise.all([loadWorkspaces(), loadProfitability(), loadAudit()]);
      } finally {
        setLoading(false);
      }
    })();
  }, [isSuperAdmin]);

  const tabBtn = (id: TabId): React.CSSProperties => ({
    padding: "0.4rem 1.1rem", borderRadius: "20px", fontSize: "0.82rem", cursor: "pointer",
    fontWeight: tab === id ? 700 : 400,
    background: tab === id ? "#1a1a2e" : "transparent",
    color: tab === id ? "#fff" : "#555",
    border: tab === id ? "none" : "1px solid #ddd",
  });

  if (!isSuperAdmin) {
    return (
      <div style={{ minHeight: "100vh", background: "#f5f5f5", fontFamily: "system-ui, sans-serif" }}>
        <NavBar />
        <main style={{ maxWidth: "760px", margin: "0 auto", padding: "2rem 1.5rem" }}>
          <p style={{ color: "#c0392b" }}>{t("admin.notAdmin")}</p>
        </main>
      </div>
    );
  }

  if (loading) {
    return (
      <div style={{ minHeight: "100vh", background: "#f5f5f5", fontFamily: "system-ui, sans-serif" }}>
        <NavBar />
        <main style={{ maxWidth: "1000px", margin: "0 auto", padding: "2rem 1.5rem" }}>
          <p style={{ color: "#888" }}>{t("common.loading")}</p>
        </main>
      </div>
    );
  }

  const fmt = (v: string | null | undefined) => v ? `$${parseFloat(v).toFixed(2)}` : "—";

  return (
    <div style={{ minHeight: "100vh", background: "#f5f5f5", fontFamily: "system-ui, sans-serif" }}>
      <NavBar />
      <main style={{ maxWidth: "1060px", margin: "0 auto", padding: "2rem 1.5rem" }}>
        <h1 style={{ margin: "0 0 1.25rem", fontSize: "1.4rem", color: "#1a1a2e" }}>{t("admin.title")}</h1>

        {/* Tabs */}
        <div style={{ display: "flex", gap: "0.4rem", marginBottom: "1.25rem" }}>
          {(["workspaces", "profitability", "audit"] as TabId[]).map((id) => (
            <button key={id} style={tabBtn(id)} onClick={() => setTab(id)}>
              {t(`admin.${id === "audit" ? "auditLog" : id}`)}
            </button>
          ))}
        </div>

        {/* Workspaces tab */}
        {tab === "workspaces" && (
          <>
            <div style={card}>
              <p style={sectionTitle}>{t("admin.createWorkspace")}</p>
              <CreateWorkspacePanel t={t} onCreated={loadWorkspaces} />
            </div>
            <div style={card}>
              <p style={sectionTitle}>{t("admin.workspaces")} ({workspaces.length})</p>
              <div style={{ overflowX: "auto" }}>
                <table style={{ width: "100%", borderCollapse: "collapse" }}>
                  <thead>
                    <tr style={{ borderBottom: "2px solid #f0f0f0", fontSize: "0.78rem", color: "#888" }}>
                      <th style={{ textAlign: "left", padding: "0.35rem 0.5rem 0.35rem 0", fontWeight: 600 }}>{t("admin.workspaces")}</th>
                      <th style={{ textAlign: "left", padding: "0.35rem 0.5rem", fontWeight: 600 }}>{t("admin.status")}</th>
                      <th style={{ textAlign: "right", padding: "0.35rem 0.5rem", fontWeight: 600 }}>{t("admin.spend")} / {t("admin.budget")}</th>
                      <th style={{ textAlign: "center", padding: "0.35rem 0.5rem", fontWeight: 600 }}>{t("admin.members")}</th>
                      <th style={{ textAlign: "left", padding: "0.35rem 0.5rem", fontWeight: 600 }}>{t("billing.status")}</th>
                      <th style={{ padding: "0.35rem 0.5rem" }} />
                    </tr>
                  </thead>
                  <tbody>
                    {workspaces.map((ws) => (
                      <WorkspaceRow
                        key={ws.id} ws={ws} t={t}
                        onToggled={(id, enabled) => {
                          setWorkspaces((prev) => prev.map((w) => w.id === id ? { ...w, payment_enabled: enabled } : w));
                        }}
                      />
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </>
        )}

        {/* Profitability tab */}
        {tab === "profitability" && profitability && (
          <>
            <div style={card}>
              <p style={sectionTitle}>{t("admin.profitability")}</p>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: "1rem" }}>
                {[
                  { label: t("admin.totalRevenue"), value: fmt(profitability.total_revenue_usd), color: "#27ae60" },
                  { label: t("admin.totalCost"), value: fmt(profitability.total_compute_cost_usd), color: "#c0392b" },
                  { label: t("admin.margin"), value: fmt(profitability.realized_margin_usd), color: "#1565c0" },
                  { label: t("admin.marginPct"), value: profitability.margin_pct !== null ? `${Math.round(profitability.margin_pct * 100)}%` : "—", color: "#5c6bc0" },
                  { label: t("admin.activeSubscriptions"), value: String(profitability.active_subscriptions), color: "#888" },
                ].map(({ label, value, color }) => (
                  <div key={label} style={{ background: "#fafafa", border: "1px solid #eee", borderRadius: "6px", padding: "0.85rem 1rem" }}>
                    <div style={{ fontSize: "1.4rem", fontWeight: 700, color }}>{value}</div>
                    <div style={{ fontSize: "0.72rem", color: "#888", marginTop: "0.2rem" }}>{label}</div>
                  </div>
                ))}
              </div>
            </div>
            <div style={card}>
              <p style={sectionTitle}>{t("admin.atRisk")}</p>
              {profitability.at_risk_workspaces.length === 0 ? (
                <p style={{ color: "#aaa", fontSize: "0.875rem" }}>{t("admin.noAtRisk")}</p>
              ) : (
                profitability.at_risk_workspaces.map((ws) => (
                  <div key={ws.workspace_id} style={{ padding: "0.4rem 0", borderBottom: "1px solid #f0f0f0", fontSize: "0.82rem", display: "flex", justifyContent: "space-between" }}>
                    <span style={{ color: "#555", fontFamily: "monospace" }}>{ws.workspace_id.slice(0, 8)}…</span>
                    <span style={{ color: "#e67e22" }}>
                      ${parseFloat(ws.spend_usd).toFixed(2)} / ${parseFloat(ws.included_budget_usd).toFixed(2)}
                      {ws.pct !== null && ` (${Math.round(ws.pct * 100)}%)`}
                    </span>
                  </div>
                ))
              )}
            </div>
          </>
        )}

        {/* Audit log tab */}
        {tab === "audit" && (
          <div style={card}>
            <p style={sectionTitle}>{t("admin.auditLog")}</p>
            {auditLog.length === 0 ? (
              <p style={{ color: "#aaa", fontSize: "0.875rem" }}>{t("admin.noAuditLog")}</p>
            ) : (
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.82rem" }}>
                <thead>
                  <tr style={{ borderBottom: "2px solid #f0f0f0", color: "#888" }}>
                    <th style={{ textAlign: "left", padding: "0.3rem 0.5rem 0.3rem 0", fontWeight: 600 }}>{t("admin.action")}</th>
                    <th style={{ textAlign: "left", padding: "0.3rem 0.5rem", fontWeight: 600 }}>{t("admin.target")}</th>
                    <th style={{ textAlign: "left", padding: "0.3rem 0.5rem", fontWeight: 600 }}>{t("admin.when")}</th>
                  </tr>
                </thead>
                <tbody>
                  {auditLog.map((entry) => (
                    <tr key={entry.id} style={{ borderBottom: "1px solid #f8f8f8" }}>
                      <td style={{ padding: "0.35rem 0.5rem 0.35rem 0", fontWeight: 600, color: "#1a1a2e" }}>{entry.action}</td>
                      <td style={{ padding: "0.35rem 0.5rem", color: "#555", fontFamily: "monospace", fontSize: "0.72rem" }}>
                        {entry.target_workspace_id?.slice(0, 8) ?? entry.target_user_id?.slice(0, 8) ?? "—"}
                      </td>
                      <td style={{ padding: "0.35rem 0.5rem", color: "#aaa" }}>
                        {new Date(entry.created_at).toLocaleString()}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        )}
      </main>
    </div>
  );
}
