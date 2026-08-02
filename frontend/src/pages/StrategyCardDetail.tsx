import { useState, useEffect } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { NavBar } from "../components/NavBar";
import { useAuth } from "../context/AuthContext";
import api from "../api/client";

// ── types ────────────────────────────────────────────────────────────────────

type FlowchartSpec = {
  entry?: string; confirmation?: string; risk_management?: string; exit?: string;
};
type WalkForward = {
  sufficient_data: boolean;
  in_sample_win_rate?: number; out_sample_win_rate?: number;
  performance_degradation?: number; potentially_overfit?: boolean;
  in_sample_n?: number; out_sample_n?: number; reason?: string;
};
type ConfidenceInterval = { lower: number; upper: number; confidence_level: number; n: number };
type VersionSnapshot = {
  version: number; snapshot_at: string; change_source: string;
  sample_size: number; win_rate: string | null; confidence_tier: string;
  preliminary_confidence: string; changes_en: string;
};
type StrategyCardOut = {
  id: string; workspace_id: string; setup_type: string; symbol_scope: string;
  description_en: string; description_es: string;
  flowchart_spec: FlowchartSpec;
  sample_size: number; preliminary_confidence: number; source_count: number;
  win_rate: number | null; confidence_interval: ConfidenceInterval | null;
  walk_forward_result: WalkForward | null; confidence_tier: string | null;
  validation_updated_at: string | null;
  version: number; version_history: VersionSnapshot[]; last_updated: string;
};
type CitedExample = {
  id: string; symbol: string | null; action: string | null; setup_type: string | null;
  summary_en: string | null; summary_es: string | null;
  original_text: string | null; source_language: string | null;
  author: string | null; channel: string | null;
  message_timestamp: string | null; source_type: string | null;
  source_metadata: Record<string, unknown> | null; outcome: string | null;
};
type OutcomeSummary = {
  won: number; lost: number; open: number; expired: number;
  inconclusive: number; total: number;
};
type DetailResponse = {
  card: StrategyCardOut; examples: CitedExample[]; outcome_summary: OutcomeSummary;
};

// ── shared style helpers ─────────────────────────────────────────────────────

const TIER_COLORS: Record<string, string> = {
  still_learning: "#e67e22", developing: "#1565c0", established: "#27ae60",
};
const OUTCOME_COLORS: Record<string, string> = {
  won: "#27ae60", lost: "#c0392b", open: "#1565c0",
  expired: "#888", inconclusive: "#aaa",
};

const badge = (color: string): React.CSSProperties => ({
  display: "inline-block", fontSize: "0.71rem", padding: "2px 8px",
  borderRadius: "10px", background: color + "22", color, fontWeight: 600,
  border: `1px solid ${color}44`,
});
const card: React.CSSProperties = {
  background: "#fff", border: "1px solid #e0e0e0", borderRadius: "8px",
  padding: "1.25rem 1.5rem", marginBottom: "1.25rem",
};
const sectionTitle = (mb = "0.75rem"): React.CSSProperties => ({
  margin: `0 0 ${mb}`, fontSize: "0.85rem", fontWeight: 700,
  color: "#555", textTransform: "uppercase", letterSpacing: "0.04em",
});

// ── Flowchart component ───────────────────────────────────────────────────────

const FLOW_STEPS: Array<{ key: keyof FlowchartSpec; labelKey: string; color: string }> = [
  { key: "entry",           labelKey: "detail.entry",           color: "#1565c0" },
  { key: "confirmation",    labelKey: "detail.confirmation",    color: "#5c6bc0" },
  { key: "risk_management", labelKey: "detail.riskManagement",  color: "#e67e22" },
  { key: "exit",            labelKey: "detail.exit",            color: "#27ae60" },
];

function Flowchart({ spec, t }: { spec: FlowchartSpec; t: (k: string) => string }) {
  const steps = FLOW_STEPS.filter(({ key }) => spec[key]);
  if (steps.length === 0) return <p style={{ color: "#aaa", fontSize: "0.85rem" }}>—</p>;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 0 }}>
      {steps.map(({ key, labelKey, color }, idx) => (
        <div key={key}>
          <div style={{
            display: "grid", gridTemplateColumns: "130px 1fr",
            background: color + "0d", border: `1px solid ${color}33`,
            borderRadius: "6px", overflow: "hidden",
          }}>
            <div style={{
              background: color + "22", padding: "0.65rem 0.9rem",
              display: "flex", alignItems: "center",
              borderRight: `1px solid ${color}33`,
            }}>
              <span style={{ fontSize: "0.78rem", fontWeight: 700, color }}>{t(labelKey)}</span>
            </div>
            <div style={{ padding: "0.65rem 1rem" }}>
              <p style={{ margin: 0, fontSize: "0.875rem", color: "#333", lineHeight: 1.55 }}>{spec[key]}</p>
            </div>
          </div>
          {idx < steps.length - 1 && (
            <div style={{ display: "flex", justifyContent: "flex-start", paddingLeft: "64px", color: "#ccc", lineHeight: 1, margin: "2px 0" }}>
              ↓
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

// ── OutcomeBar component ──────────────────────────────────────────────────────

function OutcomeBar({ summary, t }: { summary: OutcomeSummary; t: (k: string) => string }) {
  const { won, lost, open, expired, inconclusive, total } = summary;
  if (total === 0) {
    return <p style={{ color: "#aaa", fontSize: "0.875rem" }}>{t("detail.noOutcome")}</p>;
  }

  const items: Array<{ key: string; value: number }> = [
    { key: "won", value: won },
    { key: "lost", value: lost },
    { key: "open", value: open },
    { key: "expired", value: expired },
    { key: "inconclusive", value: inconclusive },
  ].filter(({ value }) => value > 0);

  return (
    <div>
      {/* Stacked bar */}
      <div style={{ display: "flex", borderRadius: "4px", overflow: "hidden", height: "12px", marginBottom: "0.6rem" }}>
        {items.map(({ key, value }) => (
          <div
            key={key}
            title={`${t(`detail.${key}`)}: ${value}`}
            style={{ flex: value, background: OUTCOME_COLORS[key] || "#aaa" }}
          />
        ))}
      </div>
      {/* Legend */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: "0.75rem" }}>
        {items.map(({ key, value }) => (
          <div key={key} style={{ display: "flex", alignItems: "center", gap: "0.3rem" }}>
            <div style={{ width: "10px", height: "10px", borderRadius: "2px", background: OUTCOME_COLORS[key] || "#aaa" }} />
            <span style={{ fontSize: "0.78rem", color: "#555" }}>
              {t(`detail.${key}`)}: <strong>{value}</strong>
            </span>
          </div>
        ))}
        <span style={{ fontSize: "0.78rem", color: "#aaa" }}>({total} total)</span>
      </div>
    </div>
  );
}

// ── CitedExampleCard ──────────────────────────────────────────────────────────

function CitedExampleCard({ ex, lang, t }: {
  ex: CitedExample; lang: string; t: (k: string) => string;
}) {
  const [showOriginal, setShowOriginal] = useState(false);

  const summary = lang === "es" ? ex.summary_es : ex.summary_en;
  const sourceLabel = [ex.channel, ex.author].filter(Boolean).join(" · ");
  const timeLabel = ex.message_timestamp
    ? new Date(ex.message_timestamp).toLocaleDateString()
    : null;

  const sourceLink = (() => {
    if (!ex.source_metadata) return null;
    if (ex.source_type === "youtube" && ex.source_metadata.video_url) {
      return { href: String(ex.source_metadata.video_url), label: "YouTube" };
    }
    return null;
  })();

  return (
    <div style={{ background: "#fafafa", border: "1px solid #eee", borderRadius: "6px", padding: "0.85rem 1rem", marginBottom: "0.6rem" }}>
      {/* Header row */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "0.5rem", marginBottom: "0.35rem" }}>
        <div style={{ display: "flex", gap: "0.35rem", flexWrap: "wrap" }}>
          {ex.symbol && <span style={badge("#1a1a2e")}>{ex.symbol}</span>}
          {ex.action && <span style={badge("#5c6bc0")}>{ex.action}</span>}
          {ex.source_type && <span style={badge("#888")}>{ex.source_type}</span>}
        </div>
        {ex.outcome && (
          <span style={badge(OUTCOME_COLORS[ex.outcome] || "#888")}>
            {ex.outcome}
          </span>
        )}
      </div>

      {/* Summary */}
      {summary && (
        <p style={{ margin: "0 0 0.35rem", fontSize: "0.875rem", color: "#333", lineHeight: 1.5 }}>{summary}</p>
      )}

      {/* Original text toggle */}
      {ex.original_text && ex.original_text !== summary && (
        <div style={{ marginBottom: "0.35rem" }}>
          <button
            onClick={() => setShowOriginal((v) => !v)}
            style={{ fontSize: "0.75rem", color: "#1565c0", background: "none", border: "none", padding: 0, cursor: "pointer" }}
          >
            {showOriginal ? t("detail.hideOriginal") : t("detail.showOriginal")}
            {ex.source_language && ex.source_language !== "en" && ` (${ex.source_language})`}
          </button>
          {showOriginal && (
            <p style={{ margin: "0.3rem 0 0", fontSize: "0.82rem", color: "#666", fontStyle: "italic", lineHeight: 1.5, paddingLeft: "0.5rem", borderLeft: "2px solid #e0e0e0" }}>
              {ex.original_text}
            </p>
          )}
        </div>
      )}

      {/* Source attribution */}
      <div style={{ display: "flex", gap: "0.75rem", alignItems: "center", flexWrap: "wrap" }}>
        {sourceLabel && <span style={{ fontSize: "0.72rem", color: "#aaa" }}>{sourceLabel}</span>}
        {timeLabel && <span style={{ fontSize: "0.72rem", color: "#aaa" }}>{timeLabel}</span>}
        {sourceLink && (
          <a href={sourceLink.href} target="_blank" rel="noopener noreferrer" style={{ fontSize: "0.72rem", color: "#1565c0" }}>
            {t("detail.viewSource")} ({sourceLink.label})
          </a>
        )}
      </div>
    </div>
  );
}

// ── My execution section ──────────────────────────────────────────────────────

const OUTCOME_COLORS_EX: Record<string, string> = {
  won: "#27ae60", lost: "#c0392b", open: "#1565c0",
  expired: "#888", inconclusive: "#aaa",
};

type ExEntry = {
  id: string; symbol: string; action: string; entry: string; exit: string | null;
  size: string; mode: string; timestamp: string | null; outcome: string;
  target_price: string | null; stop_price: string | null;
  actual_exit_price: string | null;
  max_adverse_excursion: string | null; max_favorable_excursion: string | null;
};
type ExStats = {
  total: number; won: number; lost: number; open: number;
  expired: number; inconclusive: number; win_rate: number | null;
  entries: ExEntry[];
};

function MyExecution({ stats, cardWinRate, t, navigate }: {
  stats: ExStats; cardWinRate: number | null; t: (k: string) => string; navigate: (path: string) => void;
}) {
  if (stats.total === 0) {
    return (
      <p style={{ color: "#aaa", fontSize: "0.875rem" }}>
        {t("execution.noTrades")}
      </p>
    );
  }

  const myWr = stats.win_rate !== null ? Math.round(stats.win_rate * 100) : null;
  const channelWr = cardWinRate !== null ? Math.round(Number(cardWinRate) * 100) : null;
  const myWrColor = myWr !== null ? (myWr >= 55 ? "#27ae60" : myWr >= 45 ? "#e67e22" : "#c0392b") : "#aaa";

  return (
    <div>
      {/* Stat summary */}
      <div style={{ display: "flex", gap: "1.5rem", flexWrap: "wrap", marginBottom: "0.85rem", alignItems: "flex-end" }}>
        <div>
          {myWr !== null ? (
            <>
              <span style={{ fontSize: "1.4rem", fontWeight: 700, color: myWrColor }}>{myWr}%</span>
              <span style={{ fontSize: "0.78rem", color: "#888", marginLeft: "0.35rem" }}>{t("execution.winRate")}</span>
            </>
          ) : (
            <span style={{ fontSize: "0.78rem", color: "#aaa" }}>{t("detail.noOutcome")}</span>
          )}
          {channelWr !== null && (
            <div style={{ fontSize: "0.72rem", color: "#aaa" }}>
              {t("execution.vsChannel")}: {channelWr}%
              {myWr !== null && (
                <span style={{ marginLeft: "0.25rem", color: myWr >= channelWr ? "#27ae60" : "#e67e22" }}>
                  ({myWr >= channelWr ? "+" : ""}{myWr - channelWr}pp)
                </span>
              )}
            </div>
          )}
        </div>
        <div style={{ fontSize: "0.82rem", color: "#666" }}>
          <span>{stats.total} {t("execution.trades")}</span>
          <span style={{ margin: "0 0.35rem", color: "#27ae60" }}>· {stats.won} {t("execution.won")}</span>
          <span style={{ color: "#c0392b" }}>· {stats.lost} {t("execution.lost")}</span>
          {stats.open > 0 && <span style={{ color: "#1565c0" }}> · {stats.open} {t("execution.open")}</span>}
        </div>
        <button
          onClick={() => navigate("/trading")}
          style={{ marginLeft: "auto", padding: "4px 12px", fontSize: "0.78rem", border: "1px solid #27ae60", borderRadius: "4px", background: "transparent", cursor: "pointer", color: "#27ae60" }}
        >
          {t("trading.proposeFromCard")} →
        </button>
      </div>

      {/* Entry list */}
      {stats.entries.map((ex) => {
        const outColor = OUTCOME_COLORS_EX[ex.outcome] || "#aaa";
        const outLabel = `execution.${ex.outcome === "inconclusive" ? "inconclusive" : ex.outcome}`;
        return (
          <div key={ex.id} style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", padding: "0.5rem 0", borderBottom: "1px solid #f0f0f0", fontSize: "0.82rem", gap: "0.5rem" }}>
            <div>
              <div style={{ display: "flex", gap: "0.35rem", flexWrap: "wrap", marginBottom: "0.15rem" }}>
                <span style={{ ...badge(outColor) }}>{t(outLabel)}</span>
                <span style={{ color: "#555" }}>{ex.action}</span>
                <span style={{ color: "#555" }}>{ex.symbol}</span>
              </div>
              <div style={{ display: "flex", gap: "0.75rem", color: "#888", flexWrap: "wrap" }}>
                <span>{t("execution.entryPrice")}: ${parseFloat(ex.entry).toFixed(2)}</span>
                {ex.exit && <span>{t("execution.exitPrice")}: ${parseFloat(ex.exit).toFixed(2)}</span>}
                {ex.target_price && <span>{t("execution.target")}: ${parseFloat(ex.target_price).toFixed(2)}</span>}
                {ex.stop_price && <span>{t("execution.stop")}: ${parseFloat(ex.stop_price).toFixed(2)}</span>}
              </div>
            </div>
            <span style={{ fontSize: "0.7rem", color: "#bbb", whiteSpace: "nowrap" }}>
              {ex.timestamp ? new Date(ex.timestamp).toLocaleDateString() : ""}
            </span>
          </div>
        );
      })}
    </div>
  );
}

// ── Version history ───────────────────────────────────────────────────────────

function VersionHistory({ history, t }: { history: VersionSnapshot[]; t: (k: string) => string }) {
  if (!history || history.length === 0) {
    return <p style={{ color: "#aaa", fontSize: "0.875rem" }}>{t("detail.noHistory")}</p>;
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
      {[...history].reverse().map((snap, idx) => (
        <div key={idx} style={{ display: "grid", gridTemplateColumns: "60px 1fr", gap: "0.5rem 1rem", fontSize: "0.82rem", paddingBottom: "0.5rem", borderBottom: "1px solid #f0f0f0" }}>
          <span style={{ fontWeight: 700, color: "#555" }}>{t("detail.version")}{snap.version}</span>
          <div>
            <div style={{ color: "#555", marginBottom: "0.15rem" }}>{snap.changes_en}</div>
            <div style={{ display: "flex", gap: "0.75rem", color: "#aaa", fontSize: "0.72rem" }}>
              <span>{snap.change_source}</span>
              {snap.snapshot_at && <span>{new Date(snap.snapshot_at).toLocaleDateString()}</span>}
              {snap.win_rate && <span>WR: {Math.round(parseFloat(snap.win_rate) * 100)}%</span>}
              {snap.sample_size !== undefined && <span>n={snap.sample_size}</span>}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export function StrategyCardDetail() {
  const { cardId } = useParams<{ cardId: string }>();
  const navigate = useNavigate();
  const { t, i18n } = useTranslation();
  const { user } = useAuth();
  const workspaceId = user?.default_workspace_id;

  const [detail, setDetail] = useState<DetailResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [inPlan, setInPlan] = useState(false);
  const [addingPlan, setAddingPlan] = useState(false);
  const [execStats, setExecStats] = useState<ExStats | null>(null);

  const lang = i18n.language === "es" ? "es" : "en";

  useEffect(() => {
    if (!workspaceId || !cardId) return;
    (async () => {
      setLoading(true);
      try {
        const [detailRes, planRes, execRes] = await Promise.all([
          api.get<DetailResponse>(`/workspaces/${workspaceId}/strategy-cards/${cardId}/detail`),
          api.get<{ strategy_card_id: string }[]>(`/workspaces/${workspaceId}/plan-items`).catch(() => ({ data: [] })),
          api.get<ExStats>(`/workspaces/${workspaceId}/strategy-cards/${cardId}/my-execution`).catch(() => ({ data: null })),
        ]);
        setDetail(detailRes.data);
        setInPlan(planRes.data.some((p: { strategy_card_id: string }) => p.strategy_card_id === cardId));
        setExecStats(execRes.data);
      } finally {
        setLoading(false);
      }
    })();
  }, [workspaceId, cardId]);

  const addToPlan = async () => {
    if (!workspaceId || !cardId) return;
    setAddingPlan(true);
    try {
      await api.post(`/workspaces/${workspaceId}/plan-items`, { strategy_card_id: cardId });
      setInPlan(true);
    } finally {
      setAddingPlan(false);
    }
  };

  if (loading) {
    return (
      <div style={{ minHeight: "100vh", background: "#f5f5f5", fontFamily: "system-ui, sans-serif" }}>
        <NavBar />
        <main style={{ maxWidth: "860px", margin: "0 auto", padding: "2rem 1.5rem" }}>
          <p style={{ color: "#888" }}>{t("common.loading")}</p>
        </main>
      </div>
    );
  }

  if (!detail) {
    return (
      <div style={{ minHeight: "100vh", background: "#f5f5f5", fontFamily: "system-ui, sans-serif" }}>
        <NavBar />
        <main style={{ maxWidth: "860px", margin: "0 auto", padding: "2rem 1.5rem" }}>
          <p style={{ color: "#c0392b" }}>{t("common.error")}</p>
        </main>
      </div>
    );
  }

  const { card: c, examples, outcome_summary } = detail;
  const desc = lang === "es" ? c.description_es : c.description_en;
  const wr = c.win_rate !== null && c.win_rate !== undefined ? Math.round(Number(c.win_rate) * 100) : null;
  const wrColor = wr !== null ? (wr >= 55 ? "#27ae60" : wr >= 45 ? "#e67e22" : "#c0392b") : "#aaa";

  return (
    <div style={{ minHeight: "100vh", background: "#f5f5f5", fontFamily: "system-ui, sans-serif" }}>
      <NavBar />
      <main style={{ maxWidth: "860px", margin: "0 auto", padding: "2rem 1.5rem" }}>

        {/* Back + plan + trade actions */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1.25rem" }}>
          <button
            onClick={() => navigate("/library")}
            style={{ background: "none", border: "none", cursor: "pointer", color: "#1565c0", fontSize: "0.875rem", padding: 0 }}
          >
            ← {t("detail.back")}
          </button>
          <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
            {inPlan ? (
              <span style={badge("#27ae60")}>✓ {t("detail.inPlan")}</span>
            ) : (
              <button
                onClick={addToPlan}
                disabled={addingPlan}
                style={{ padding: "0.45rem 1.1rem", background: "#1a1a2e", color: "#fff", border: "none", borderRadius: "4px", cursor: "pointer", fontWeight: 600, fontSize: "0.875rem", opacity: addingPlan ? 0.65 : 1 }}
              >
                {t("detail.addToPlan")}
              </button>
            )}
            <button
              onClick={() => {
                const params = new URLSearchParams({ cardId: c.id });
                if (c.symbol_scope !== "general") params.set("symbol", c.symbol_scope);
                navigate(`/trading?${params.toString()}`);
              }}
              style={{ padding: "0.45rem 1.1rem", background: "#27ae60", color: "#fff", border: "none", borderRadius: "4px", cursor: "pointer", fontWeight: 600, fontSize: "0.875rem" }}
            >
              {t("trading.proposeFromCard")}
            </button>
          </div>
        </div>

        {/* Header card */}
        <div style={card}>
          <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap", marginBottom: "0.6rem" }}>
            {c.symbol_scope !== "general" && <span style={badge("#1a1a2e")}>{c.symbol_scope}</span>}
            <span style={badge("#5c6bc0")}>{c.setup_type}</span>
            {c.confidence_tier && (
              <span style={badge(TIER_COLORS[c.confidence_tier] || "#888")}>
                {t(`tier.${c.confidence_tier}`)}
              </span>
            )}
          </div>

          <p style={{ margin: "0 0 0.75rem", fontSize: "0.95rem", color: "#222", lineHeight: 1.6 }}>{desc}</p>

          {/* Stats row */}
          <div style={{ display: "flex", gap: "1.5rem", flexWrap: "wrap", alignItems: "center" }}>
            {wr !== null ? (
              <div>
                <span style={{ fontSize: "1.5rem", fontWeight: 700, color: wrColor }}>{wr}%</span>
                <span style={{ fontSize: "0.78rem", color: "#888", marginLeft: "0.35rem" }}>{t("library.winRate")}</span>
                {c.confidence_interval && (
                  <span style={{ fontSize: "0.7rem", color: "#aaa", marginLeft: "0.35rem" }}>
                    [{Math.round(c.confidence_interval.lower * 100)}%–{Math.round(c.confidence_interval.upper * 100)}%]
                  </span>
                )}
              </div>
            ) : (
              <span style={{ fontSize: "0.78rem", color: "#aaa" }}>{t("library.noValidation")}</span>
            )}
            <span style={{ fontSize: "0.78rem", color: "#888" }}>{c.sample_size} {t("library.sampleSize")}</span>
            <span style={{ fontSize: "0.78rem", color: "#888" }}>{c.source_count} {t("library.sources")}</span>
            <span style={{ fontSize: "0.78rem", color: "#888" }}>{t("library.version")}{c.version}</span>
            {c.validation_updated_at && (
              <span style={{ fontSize: "0.72rem", color: "#bbb" }}>
                {t("detail.validatedAt")} {new Date(c.validation_updated_at).toLocaleDateString()}
              </span>
            )}
          </div>
        </div>

        {/* Outcome breakdown */}
        <div style={card}>
          <p style={sectionTitle()}>{t("detail.outcomeStats")}</p>
          <OutcomeBar summary={outcome_summary} t={t} />
        </div>

        {/* Flowchart */}
        <div style={card}>
          <p style={sectionTitle()}>{t("detail.flowchartTitle")}</p>
          <Flowchart spec={c.flowchart_spec} t={t} />
        </div>

        {/* Walk-forward detail (if available) */}
        {c.walk_forward_result?.sufficient_data && (
          <div style={{ ...card, background: c.walk_forward_result.potentially_overfit ? "#fff8e1" : "#f1f8f4" }}>
            <p style={sectionTitle()}>Walk-forward</p>
            <p style={{ margin: 0, fontSize: "0.85rem", color: "#555" }}>
              {t("library.inSample")}: <strong>{Math.round((c.walk_forward_result.in_sample_win_rate ?? 0) * 100)}%</strong> (n={c.walk_forward_result.in_sample_n})
              {" → "}
              {t("library.outSample")}: <strong>{Math.round((c.walk_forward_result.out_sample_win_rate ?? 0) * 100)}%</strong> (n={c.walk_forward_result.out_sample_n})
              {c.walk_forward_result.potentially_overfit && (
                <span style={{ marginLeft: "0.75rem", color: "#e67e22" }}>⚠ {t("library.overfitWarning")}</span>
              )}
            </p>
          </div>
        )}

        {/* Cited examples */}
        <div style={card}>
          <p style={sectionTitle()}>{t("detail.examples")} ({examples.length})</p>
          {examples.length === 0 ? (
            <p style={{ color: "#aaa", fontSize: "0.875rem" }}>{t("detail.noExamples")}</p>
          ) : (
            examples.map((ex) => (
              <CitedExampleCard key={ex.id} ex={ex} lang={lang} t={t} />
            ))
          )}
        </div>

        {/* My execution */}
        <div style={card}>
          <p style={sectionTitle()}>{t("execution.title")}</p>
          {execStats ? (
            <MyExecution stats={execStats} cardWinRate={c.win_rate} t={t} navigate={navigate} />
          ) : (
            <p style={{ color: "#aaa", fontSize: "0.875rem" }}>{t("execution.noTrades")}</p>
          )}
        </div>

        {/* Version history */}
        <div style={card}>
          <p style={sectionTitle()}>{t("detail.versionHistory")}</p>
          <VersionHistory history={c.version_history} t={t} />
        </div>

      </main>
    </div>
  );
}
