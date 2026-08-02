import { useState, useEffect } from "react";
import { useTranslation } from "react-i18next";
import { NavBar } from "../components/NavBar";
import { useAuth } from "../context/AuthContext";
import api from "../api/client";

type ChangelogEntry = {
  card_id: string; symbol_scope: string; setup_type: string;
  version: number; changed_at: string; change_source: string;
  changes_en: string; sample_size: number | null;
  win_rate: number | null; confidence_tier: string | null;
};

const TIER_COLORS: Record<string, string> = {
  still_learning: "#e67e22",
  developing:     "#1565c0",
  established:    "#27ae60",
};
const SOURCE_COLORS: Record<string, string> = {
  mining:     "#5c6bc0",
  validation: "#27ae60",
  unknown:    "#888",
};

function formatDay(isoStr: string, today: string, yesterday: string): string {
  if (!isoStr) return "";
  const d = isoStr.slice(0, 10);
  const t = new Date().toISOString().slice(0, 10);
  const y = new Date(Date.now() - 86400000).toISOString().slice(0, 10);
  if (d === t) return today;
  if (d === y) return yesterday;
  return d;
}

function groupByDay(entries: ChangelogEntry[]): Record<string, ChangelogEntry[]> {
  const groups: Record<string, ChangelogEntry[]> = {};
  for (const e of entries) {
    const day = e.changed_at?.slice(0, 10) || "unknown";
    (groups[day] = groups[day] || []).push(e);
  }
  return groups;
}

export function Changelog() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const workspaceId = user?.default_workspace_id;

  const [entries, setEntries] = useState<ChangelogEntry[]>([]);
  const [recomputing, setRecomputing] = useState(false);
  const [msg, setMsg] = useState("");

  const load = async () => {
    if (!workspaceId) return;
    const res = await api.get<ChangelogEntry[]>(`/workspaces/${workspaceId}/changelog?limit=100`);
    setEntries(res.data);
  };

  useEffect(() => { load(); }, [workspaceId]);

  const recompute = async () => {
    if (!workspaceId) return;
    setRecomputing(true); setMsg("");
    try {
      await api.post(`/workspaces/${workspaceId}/recompute`);
      setMsg(t("changelog.recomputeStarted"));
      setTimeout(() => { load(); setMsg(""); }, 35000);
    } finally {
      setRecomputing(false);
    }
  };

  const today = t("changelog.today");
  const yesterday = t("changelog.yesterday");
  const groups = groupByDay(entries);
  const days = Object.keys(groups).sort().reverse();

  return (
    <div style={{ minHeight: "100vh", background: "#f5f5f5", fontFamily: "system-ui, sans-serif" }}>
      <NavBar />
      <main style={{ maxWidth: "780px", margin: "0 auto", padding: "2rem 1.5rem" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1.25rem" }}>
          <h1 style={{ margin: 0, fontSize: "1.4rem", color: "#1a1a2e" }}>{t("changelog.title")}</h1>
          <button
            onClick={recompute}
            disabled={recomputing}
            style={{
              padding: "0.5rem 1.1rem", background: "#1a1a2e", color: "#fff",
              border: "none", borderRadius: "4px", cursor: "pointer",
              fontWeight: 600, fontSize: "0.875rem", opacity: recomputing ? 0.65 : 1,
            }}
          >
            {recomputing ? t("changelog.recomputing") : t("changelog.recompute")}
          </button>
        </div>

        {msg && <p style={{ color: "#27ae60", fontSize: "0.875rem", marginBottom: "0.75rem" }}>{msg}</p>}

        {entries.length === 0 ? (
          <div style={{ background: "#fff", border: "1px solid #e0e0e0", borderRadius: "8px", padding: "1.5rem", color: "#888", fontStyle: "italic" }}>
            {t("changelog.noEntries")}
          </div>
        ) : (
          days.map((day) => (
            <div key={day} style={{ marginBottom: "1.5rem" }}>
              <h3 style={{ fontSize: "0.82rem", fontWeight: 700, color: "#888", textTransform: "uppercase", letterSpacing: "0.05em", margin: "0 0 0.6rem" }}>
                {formatDay(day + "T12:00:00Z", today, yesterday)}
              </h3>
              {(groups[day] || []).map((entry, i) => {
                const sourceColor = SOURCE_COLORS[entry.change_source] || SOURCE_COLORS.unknown;
                const tierColor = entry.confidence_tier ? TIER_COLORS[entry.confidence_tier] || "#888" : "#888";

                return (
                  <div key={`${entry.card_id}-${entry.version}-${i}`} style={{
                    display: "flex", gap: "0.9rem", padding: "0.85rem 1.1rem",
                    background: "#fff", border: "1px solid #e8e8e8", borderRadius: "8px",
                    marginBottom: "0.5rem", alignItems: "flex-start",
                  }}>
                    {/* Source indicator dot */}
                    <div style={{ marginTop: "4px", width: "8px", height: "8px", borderRadius: "50%", background: sourceColor, flexShrink: 0 }} />

                    <div style={{ flex: 1 }}>
                      <div style={{ display: "flex", alignItems: "center", gap: "0.45rem", flexWrap: "wrap", marginBottom: "0.25rem" }}>
                        {entry.symbol_scope !== "general" && (
                          <span style={{ fontSize: "0.8rem", fontWeight: 700, color: "#1a1a2e" }}>{entry.symbol_scope}</span>
                        )}
                        <span style={{ fontSize: "0.78rem", color: "#5c6bc0" }}>{entry.setup_type}</span>
                        <span style={{ fontSize: "0.68rem", color: "#bbb" }}>{t("changelog.versionLabel")}{entry.version}</span>
                        <span style={{ fontSize: "0.68rem", color: sourceColor, fontWeight: 500 }}>
                          via {t(`changelog.${entry.change_source}`) || entry.change_source}
                        </span>
                        {entry.confidence_tier && (
                          <span style={{
                            fontSize: "0.67rem", padding: "1px 6px", borderRadius: "8px",
                            background: tierColor + "22", color: tierColor, border: `1px solid ${tierColor}44`,
                          }}>
                            {t(`tier.${entry.confidence_tier}`)}
                          </span>
                        )}
                      </div>

                      <p style={{ margin: 0, fontSize: "0.875rem", color: "#444", lineHeight: 1.5 }}>
                        {entry.changes_en}
                      </p>

                      {(entry.sample_size !== null || entry.win_rate !== null) && (
                        <div style={{ marginTop: "0.3rem", display: "flex", gap: "0.75rem", fontSize: "0.75rem", color: "#999" }}>
                          {entry.sample_size !== null && <span>{entry.sample_size} ideas</span>}
                          {entry.win_rate !== null && (
                            <span style={{ color: Number(entry.win_rate) >= 0.55 ? "#27ae60" : "#e67e22", fontWeight: 600 }}>
                              {Math.round(Number(entry.win_rate) * 100)}% win rate
                            </span>
                          )}
                          <span style={{ color: "#ccc" }}>{entry.changed_at?.slice(11, 16)} UTC</span>
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          ))
        )}
      </main>
    </div>
  );
}
