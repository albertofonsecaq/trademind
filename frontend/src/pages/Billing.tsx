import { useState, useEffect, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router-dom";
import { NavBar } from "../components/NavBar";
import { useAuth } from "../context/AuthContext";
import api from "../api/client";

// ── types ─────────────────────────────────────────────────────────────────────

type PlanInfo = {
  plan_name: string; price_usd_monthly: string; included_budget_usd: string;
  included_seats: number; price_per_seat_usd: string; included_budget_per_seat_usd: string;
  overage_rate_multiplier: string; overage_ceiling_usd: string;
};
type BillingStatus = {
  has_subscription: boolean; status: string; plan: PlanInfo | null;
  current_period_end: string | null; period_spend_usd: string;
  budget_cap_usd: string | null; budget_pct: number | null;
  current_period_overage_usd: string; overage_ceiling_usd: string | null;
  overage_pct: number | null; has_portal: boolean; payment_enabled: boolean;
};
type UsageBreakdown = { task_type: string; cost_usd: string; event_count: number };
type BillingDetails = { status: BillingStatus; breakdown: UsageBreakdown[] };

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

function ProgressBar({ pct, warn = 0.8, danger = 1.0, label }: {
  pct: number; warn?: number; danger?: number; label?: string;
}) {
  const clampedPct = Math.min(pct, 1);
  const color = pct >= danger ? "#c0392b" : pct >= warn ? "#e67e22" : "#27ae60";
  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.78rem", color: "#666", marginBottom: "0.25rem" }}>
        {label && <span>{label}</span>}
        <span style={{ color }}>{Math.round(pct * 100)}%</span>
      </div>
      <div style={{ background: "#f0f0f0", borderRadius: "4px", height: "8px" }}>
        <div style={{ width: `${clampedPct * 100}%`, height: "8px", background: color, borderRadius: "4px", transition: "width 0.3s" }} />
      </div>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export function Billing() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const workspaceId = user?.default_workspace_id;
  const [searchParams] = useSearchParams();

  const [details, setDetails] = useState<BillingDetails | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionBusy, setActionBusy] = useState<string | null>(null);
  const [actionError, setActionError] = useState("");

  const checkoutSuccess = searchParams.get("session_id") !== null;

  const load = useCallback(async () => {
    if (!workspaceId) return;
    setLoading(true);
    try {
      const res = await api.get<BillingDetails>(`/workspaces/${workspaceId}/billing`);
      setDetails(res.data);
    } finally {
      setLoading(false);
    }
  }, [workspaceId]);

  useEffect(() => { load(); }, [load]);

  const startCheckout = async () => {
    if (!workspaceId) return;
    setActionBusy("checkout"); setActionError("");
    try {
      const res = await api.post<{ url: string }>(`/workspaces/${workspaceId}/billing/checkout`);
      window.location.href = res.data.url;
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setActionError(msg || t("common.error"));
      setActionBusy(null);
    }
  };

  const openPortal = async () => {
    if (!workspaceId) return;
    setActionBusy("portal"); setActionError("");
    try {
      const res = await api.post<{ url: string }>(`/workspaces/${workspaceId}/billing/portal`);
      window.location.href = res.data.url;
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setActionError(msg || t("common.error"));
      setActionBusy(null);
    }
  };

  if (loading) {
    return (
      <div style={{ minHeight: "100vh", background: "#f5f5f5", fontFamily: "system-ui, sans-serif" }}>
        <NavBar />
        <main style={{ maxWidth: "760px", margin: "0 auto", padding: "2rem 1.5rem" }}>
          <p style={{ color: "#888" }}>{t("common.loading")}</p>
        </main>
      </div>
    );
  }

  const st = details?.status;
  const breakdown = details?.breakdown ?? [];
  const statusColor = st?.status === "active" ? "#27ae60" : st?.status === "past_due" ? "#e67e22" : "#888";

  const fmt = (v: string | null | undefined) => v ? `$${parseFloat(v).toFixed(2)}` : "—";

  const btn = (color: string): React.CSSProperties => ({
    padding: "0.5rem 1.2rem", background: color, color: "#fff",
    border: "none", borderRadius: "4px", cursor: "pointer",
    fontWeight: 600, fontSize: "0.875rem",
  });

  return (
    <div style={{ minHeight: "100vh", background: "#f5f5f5", fontFamily: "system-ui, sans-serif" }}>
      <NavBar />
      <main style={{ maxWidth: "760px", margin: "0 auto", padding: "2rem 1.5rem" }}>
        <h1 style={{ margin: "0 0 1.5rem", fontSize: "1.4rem", color: "#1a1a2e" }}>{t("billing.title")}</h1>

        {checkoutSuccess && (
          <div style={{ ...card, background: "#f1f8f4", border: "1px solid #27ae6033", marginBottom: "1rem" }}>
            <p style={{ margin: 0, color: "#27ae60", fontWeight: 600 }}>✓ {t("billing.checkoutSuccess")}</p>
          </div>
        )}

        {/* Warnings */}
        {st?.payment_enabled && st?.status !== "active" && st?.status !== "past_due" && (
          <div style={{ ...card, background: "#fff8e1", border: "1px solid #ffe08244" }}>
            <p style={{ margin: 0, fontSize: "0.875rem", color: "#c07700" }}>⚠ {t("billing.paymentLapsed")}</p>
          </div>
        )}
        {st?.budget_pct !== null && st?.budget_pct !== undefined && st.budget_pct >= 0.8 && st.budget_pct < 1 && (
          <div style={{ ...card, background: "#fff8e1", border: "1px solid #ffe08244" }}>
            <p style={{ margin: 0, fontSize: "0.875rem", color: "#c07700" }}>⚠ {t("billing.softWarning")}</p>
          </div>
        )}
        {st?.budget_pct !== null && st?.budget_pct !== undefined && st.budget_pct >= 1 && (
          <div style={{ ...card, background: "#fef2f2", border: "1px solid #c0392b33" }}>
            <p style={{ margin: 0, fontSize: "0.875rem", color: "#c0392b" }}>⛔ {t("billing.hardPause")}</p>
          </div>
        )}
        {st?.overage_pct !== null && st?.overage_pct !== undefined && st.overage_pct >= 1 && (
          <div style={{ ...card, background: "#fef2f2", border: "1px solid #c0392b33" }}>
            <p style={{ margin: 0, fontSize: "0.875rem", color: "#c0392b" }}>⛔ {t("billing.overageCeilingReached")}</p>
          </div>
        )}

        {/* Subscription card */}
        <div style={card}>
          <p style={sectionTitle}>{t("billing.plan")}</p>

          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "1rem", flexWrap: "wrap" }}>
            <div>
              <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", marginBottom: "0.5rem" }}>
                <span style={{ fontWeight: 700, fontSize: "1.1rem", color: "#1a1a2e" }}>
                  {st?.plan ? `${st.plan.plan_name.charAt(0).toUpperCase() + st.plan.plan_name.slice(1)} — ${fmt(st.plan.price_usd_monthly)}/mo` : "No plan"}
                </span>
                <span style={badge(statusColor)}>{t(`billing.${st?.status || "inactive"}`)}</span>
              </div>
              {st?.plan && (
                <div style={{ fontSize: "0.82rem", color: "#666", display: "flex", gap: "1.25rem", flexWrap: "wrap" }}>
                  <span>{t("billing.includedSeats")}: {st.plan.included_seats}</span>
                  <span>{fmt(st.plan.price_per_seat_usd)} {t("billing.perSeat")}</span>
                  <span>{t("billing.budgetCap")}: {fmt(st.plan.included_budget_usd)}/mo</span>
                </div>
              )}
              {st?.current_period_end && (
                <p style={{ margin: "0.4rem 0 0", fontSize: "0.78rem", color: "#aaa" }}>
                  {t("billing.periodEnd")}: {new Date(st.current_period_end).toLocaleDateString()}
                </p>
              )}
            </div>

            <div style={{ display: "flex", gap: "0.5rem" }}>
              {!st?.has_subscription || st?.status === "canceled" ? (
                <button onClick={startCheckout} disabled={actionBusy === "checkout"} style={btn("#1a1a2e")}>
                  {actionBusy === "checkout" ? "…" : t("billing.subscribe")}
                </button>
              ) : st?.has_portal ? (
                <button onClick={openPortal} disabled={actionBusy === "portal"} style={{ ...btn("#1565c0") }}>
                  {actionBusy === "portal" ? "…" : t("billing.manageBilling")}
                </button>
              ) : null}
            </div>
          </div>

          {actionError && <p style={{ margin: "0.5rem 0 0", fontSize: "0.82rem", color: "#c0392b" }}>{actionError}</p>}
        </div>

        {/* Spend card */}
        <div style={card}>
          <p style={sectionTitle}>{t("billing.periodSpend")}</p>
          <div style={{ display: "flex", gap: "2rem", flexWrap: "wrap", marginBottom: "0.85rem" }}>
            <div>
              <span style={{ fontSize: "1.5rem", fontWeight: 700, color: "#1a1a2e" }}>{fmt(st?.period_spend_usd)}</span>
              {st?.budget_cap_usd && <span style={{ fontSize: "0.78rem", color: "#888", marginLeft: "0.35rem" }}>/ {fmt(st.budget_cap_usd)}</span>}
            </div>
            {st?.current_period_overage_usd && parseFloat(st.current_period_overage_usd) > 0 && (
              <div>
                <span style={{ fontSize: "0.9rem", fontWeight: 600, color: "#e67e22" }}>{fmt(st.current_period_overage_usd)}</span>
                <span style={{ fontSize: "0.78rem", color: "#888", marginLeft: "0.35rem" }}>{t("billing.overage")}</span>
              </div>
            )}
          </div>

          {st?.budget_pct !== null && st?.budget_pct !== undefined && (
            <div style={{ marginBottom: "0.75rem" }}>
              <ProgressBar pct={st.budget_pct} label={t("billing.budgetCap")} />
            </div>
          )}
          {st?.overage_pct !== null && st?.overage_pct !== undefined && st.overage_pct > 0 && (
            <ProgressBar pct={st.overage_pct} warn={0.5} danger={1.0} label={t("billing.overageCeiling")} />
          )}
        </div>

        {/* Cost breakdown */}
        <div style={card}>
          <p style={sectionTitle}>{t("billing.costBreakdown")}</p>
          {breakdown.length === 0 ? (
            <p style={{ color: "#aaa", fontSize: "0.875rem" }}>{t("billing.noBreakdown")}</p>
          ) : (
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.875rem" }}>
              <thead>
                <tr style={{ borderBottom: "1px solid #f0f0f0" }}>
                  <th style={{ textAlign: "left", padding: "0.35rem 0", color: "#555", fontWeight: 600 }}>{t("billing.taskType")}</th>
                  <th style={{ textAlign: "right", padding: "0.35rem 0", color: "#555", fontWeight: 600 }}>{t("billing.events")}</th>
                  <th style={{ textAlign: "right", padding: "0.35rem 0", color: "#555", fontWeight: 600 }}>{t("billing.cost")}</th>
                </tr>
              </thead>
              <tbody>
                {breakdown.map((row) => (
                  <tr key={row.task_type} style={{ borderBottom: "1px solid #f8f8f8" }}>
                    <td style={{ padding: "0.35rem 0", color: "#333" }}>{row.task_type}</td>
                    <td style={{ textAlign: "right", padding: "0.35rem 0", color: "#666" }}>{row.event_count}</td>
                    <td style={{ textAlign: "right", padding: "0.35rem 0", color: "#333", fontWeight: 500 }}>
                      ${parseFloat(row.cost_usd).toFixed(4)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </main>
    </div>
  );
}
