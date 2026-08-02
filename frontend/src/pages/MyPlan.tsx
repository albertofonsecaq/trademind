import { useState, useEffect } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { NavBar } from "../components/NavBar";
import { useAuth } from "../context/AuthContext";
import api from "../api/client";

type CardSummary = {
  id: string; setup_type: string; symbol_scope: string;
  description_en: string; description_es: string;
  sample_size: number; preliminary_confidence: number; source_count: number;
};
type PlanItem = {
  id: string; strategy_card_id: string; status: string;
  user_notes: string | null; risk_tolerance_match: string | null;
  updated_at: string; card: CardSummary;
};

const STATUS_COLORS: Record<string, string> = {
  watching: "#1565c0", adopted: "#27ae60", rejected: "#c0392b",
};
const RISK_OPTS = ["low", "medium", "high"];

const badge = (color: string): React.CSSProperties => ({
  display: "inline-block", fontSize: "0.72rem", padding: "2px 8px",
  borderRadius: "10px", background: color + "22", color, fontWeight: 600,
  border: `1px solid ${color}44`,
});
const sec: React.CSSProperties = {
  background: "#fff", border: "1px solid #e0e0e0", borderRadius: "8px",
  padding: "1.1rem 1.4rem", marginBottom: "0.75rem",
};
const inp: React.CSSProperties = {
  padding: "0.45rem 0.65rem", border: "1px solid #ddd", borderRadius: "4px",
  fontSize: "0.85rem", width: "100%", boxSizing: "border-box",
};

export function MyPlan() {
  const { t, i18n } = useTranslation();
  const { user } = useAuth();
  const workspaceId = user?.default_workspace_id;

  const navigate = useNavigate();
  const [items, setItems] = useState<PlanItem[]>([]);
  const [filter, setFilter] = useState<string>("all");
  const [editing, setEditing] = useState<Record<string, { notes: string; risk: string }>>({});
  const [saving, setSaving] = useState<string | null>(null);

  const lang = i18n.language === "es" ? "es" : "en";

  const load = async () => {
    if (!workspaceId) return;
    const res = await api.get<PlanItem[]>(`/workspaces/${workspaceId}/plan-items`);
    setItems(res.data);
  };

  useEffect(() => { load(); }, [workspaceId]);

  const updateStatus = async (item: PlanItem, status: string) => {
    if (!workspaceId) return;
    await api.patch(`/workspaces/${workspaceId}/plan-items/${item.id}`, { status });
    setItems((prev) => prev.map((i) => i.id === item.id ? { ...i, status } : i));
  };

  const startEdit = (item: PlanItem) => {
    setEditing((prev) => ({ ...prev, [item.id]: { notes: item.user_notes || "", risk: item.risk_tolerance_match || "" } }));
  };

  const saveEdit = async (item: PlanItem) => {
    if (!workspaceId) return;
    const ed = editing[item.id];
    setSaving(item.id);
    try {
      await api.patch(`/workspaces/${workspaceId}/plan-items/${item.id}`, {
        user_notes: ed.notes || null,
        risk_tolerance_match: ed.risk || null,
      });
      setItems((prev) => prev.map((i) => i.id === item.id ? { ...i, user_notes: ed.notes || null, risk_tolerance_match: ed.risk || null } : i));
      setEditing((prev) => { const n = { ...prev }; delete n[item.id]; return n; });
    } finally {
      setSaving(null);
    }
  };

  const remove = async (item: PlanItem) => {
    if (!workspaceId || !confirm(t("plan.remove") + "?")) return;
    await api.delete(`/workspaces/${workspaceId}/plan-items/${item.id}`);
    setItems((prev) => prev.filter((i) => i.id !== item.id));
  };

  const displayed = filter === "all" ? items : items.filter((i) => i.status === filter);

  return (
    <div style={{ minHeight: "100vh", background: "#f5f5f5", fontFamily: "system-ui, sans-serif" }}>
      <NavBar />
      <main style={{ maxWidth: "820px", margin: "0 auto", padding: "2rem 1.5rem" }}>
        <h1 style={{ margin: "0 0 1.25rem", fontSize: "1.4rem", color: "#1a1a2e" }}>{t("plan.title")}</h1>

        {/* Status filter tabs */}
        <div style={{ display: "flex", gap: "0.4rem", marginBottom: "1.25rem" }}>
          {(["all", "watching", "adopted", "rejected"] as const).map((s) => (
            <button
              key={s}
              onClick={() => setFilter(s)}
              style={{
                padding: "0.35rem 1rem", borderRadius: "20px", fontSize: "0.82rem", cursor: "pointer", fontWeight: filter === s ? 700 : 400,
                background: filter === s ? "#1a1a2e" : "transparent",
                color: filter === s ? "#fff" : "#555",
                border: filter === s ? "none" : "1px solid #ddd",
              }}
            >
              {t(s === "all" ? "plan.filterAll" : `plan.${s}`)}
            </button>
          ))}
        </div>

        {displayed.length === 0 ? (
          <div style={{ ...sec, color: "#888", fontStyle: "italic" }}>{t("plan.noItems")}</div>
        ) : (
          displayed.map((item) => {
            const card = item.card;
            const desc = lang === "es" ? card.description_es : card.description_en;
            const statusColor = STATUS_COLORS[item.status] || "#888";
            const ed = editing[item.id];

            return (
              <div key={item.id} style={sec}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "1rem" }}>
                  <div style={{ flex: 1 }}>
                    <div style={{ display: "flex", gap: "0.4rem", flexWrap: "wrap", marginBottom: "0.3rem" }}>
                      {card.symbol_scope !== "general" && <span style={badge("#1a1a2e")}>{card.symbol_scope}</span>}
                      <span style={badge("#5c6bc0")}>{card.setup_type}</span>
                    </div>
                    <p style={{ margin: "0 0 0.5rem", fontSize: "0.875rem", color: "#333", lineHeight: 1.5 }}>{desc}</p>
                    <span style={{ fontSize: "0.72rem", color: "#aaa" }}>
                      {card.sample_size} {t("library.sampleSize")} · {t("library.lastUpdated")}: {new Date(item.updated_at).toLocaleDateString()}
                    </span>
                  </div>

                  {/* Status selector */}
                  <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: "0.4rem" }}>
                    <select
                      value={item.status}
                      onChange={(e) => updateStatus(item, e.target.value)}
                      style={{ padding: "0.3rem 0.6rem", borderRadius: "4px", border: `1px solid ${statusColor}`, color: statusColor, fontWeight: 600, fontSize: "0.8rem", background: statusColor + "11", cursor: "pointer" }}
                    >
                      {["watching", "adopted", "rejected"].map((s) => (
                        <option key={s} value={s}>{t(`plan.${s}`)}</option>
                      ))}
                    </select>
                    <button
                      onClick={() => navigate(`/library/${item.strategy_card_id}`)}
                      style={{ fontSize: "0.75rem", color: "#1565c0", background: "none", border: "none", cursor: "pointer" }}
                    >
                      {t("plan.viewCard")}
                    </button>
                    <button onClick={() => remove(item)} style={{ fontSize: "0.75rem", color: "#c0392b", background: "none", border: "none", cursor: "pointer" }}>
                      {t("plan.remove")}
                    </button>
                  </div>
                </div>

                {/* Inline notes/risk editor */}
                {ed ? (
                  <div style={{ marginTop: "0.75rem", display: "flex", flexDirection: "column", gap: "0.4rem" }}>
                    <textarea
                      rows={2}
                      value={ed.notes}
                      onChange={(e) => setEditing((prev) => ({ ...prev, [item.id]: { ...prev[item.id], notes: e.target.value } }))}
                      placeholder={t("plan.notesPlaceholder")}
                      style={{ ...inp, resize: "vertical" }}
                    />
                    <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
                      <span style={{ fontSize: "0.8rem", color: "#555" }}>{t("plan.riskTolerance")}:</span>
                      {["", ...RISK_OPTS].map((r) => (
                        <button
                          key={r || "none"}
                          onClick={() => setEditing((prev) => ({ ...prev, [item.id]: { ...prev[item.id], risk: r } }))}
                          style={{ padding: "2px 10px", fontSize: "0.75rem", borderRadius: "4px", cursor: "pointer", border: "1px solid #ddd", background: ed.risk === r ? "#1a1a2e" : "transparent", color: ed.risk === r ? "#fff" : "#555" }}
                        >
                          {r ? t(`plan.${r}`) : "—"}
                        </button>
                      ))}
                      <button
                        onClick={() => saveEdit(item)}
                        disabled={saving === item.id}
                        style={{ marginLeft: "auto", padding: "4px 14px", background: "#1a1a2e", color: "#fff", border: "none", borderRadius: "4px", fontSize: "0.82rem", cursor: "pointer" }}
                      >
                        {saving === item.id ? "..." : t("plan.save")}
                      </button>
                      <button onClick={() => setEditing((prev) => { const n = { ...prev }; delete n[item.id]; return n; })} style={{ fontSize: "0.8rem", color: "#888", background: "none", border: "none", cursor: "pointer" }}>
                        {t("common.cancel")}
                      </button>
                    </div>
                  </div>
                ) : (
                  <div style={{ marginTop: "0.5rem", display: "flex", gap: "0.75rem", alignItems: "center" }}>
                    {item.user_notes && <span style={{ fontSize: "0.8rem", color: "#666", fontStyle: "italic" }}>"{item.user_notes}"</span>}
                    {item.risk_tolerance_match && <span style={{ ...badge("#888"), fontSize: "0.7rem" }}>{t(`plan.${item.risk_tolerance_match}`)}</span>}
                    <button onClick={() => startEdit(item)} style={{ marginLeft: "auto", fontSize: "0.78rem", color: "#1a1a2e", background: "none", border: "1px solid #ddd", borderRadius: "4px", padding: "2px 10px", cursor: "pointer" }}>
                      {t("common.save")} / edit
                    </button>
                  </div>
                )}
              </div>
            );
          })
        )}
      </main>
    </div>
  );
}
