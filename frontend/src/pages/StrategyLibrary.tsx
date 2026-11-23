import { useState, useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { NavBar } from "../components/NavBar";
import { useAuth } from "../context/AuthContext";
import api from "../api/client";

type FlowchartSpec = { entry?: string; confirmation?: string; risk_management?: string; exit?: string };
type WalkForward = {
  sufficient_data: boolean;
  in_sample_win_rate?: number; out_sample_win_rate?: number;
  performance_degradation?: number; potentially_overfit?: boolean;
  in_sample_n?: number; out_sample_n?: number;
  reason?: string;
};
type ConfidenceInterval = { lower: number; upper: number; confidence_level: number; n: number };
type StrategyCard = {
  id: string; setup_type: string; symbol_scope: string;
  description_en: string; description_es: string;
  flowchart_spec: FlowchartSpec;
  sample_size: number; preliminary_confidence: number; source_count: number;
  win_rate: number | null; confidence_interval: ConfidenceInterval | null;
  walk_forward_result: WalkForward | null;
  confidence_tier: string | null;
  validation_updated_at: string | null;
  version: number; last_updated: string;
};

type RunKind = "mining" | "validation";
type AnalysisRun = {
  id: string; kind: RunKind;
  status: "running" | "completed" | "partial" | "failed";
  triggered_by_user_id: string | null;
  cards_created: number; cards_updated: number;
  failures: { subject: string; reason: string }[];
  error_message: string | null;
  started_at: string; finished_at: string | null;
};
type LatestRuns = Record<RunKind, AnalysisRun | null>;

// While a run is going, how often the Library asks how it is doing (#86).
const RUN_POLL_MS = 3000;

const TIER_COLORS: Record<string, string> = {
  still_learning: "#e67e22",
  developing:     "#1565c0",
  established:    "#27ae60",
};

const badge = (color: string): React.CSSProperties => ({
  display: "inline-block", fontSize: "0.71rem", padding: "2px 8px",
  borderRadius: "10px", background: color + "22", color, fontWeight: 600,
  border: `1px solid ${color}44`,
});
const sec: React.CSSProperties = {
  background: "#fff", border: "1px solid #e0e0e0", borderRadius: "8px",
  padding: "1.25rem 1.5rem",
};

function WinRateDisplay({ card, t }: { card: StrategyCard; t: (k: string) => string }) {
  if (card.win_rate === null || card.win_rate === undefined) {
    return (
      <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: "2px" }}>
        <span style={{ fontSize: "0.72rem", color: "#bbb" }}>{t("library.noValidation")}</span>
        <span style={{ fontSize: "0.8rem", color: "#aaa" }}>
          {t("library.confidence")}: {Math.round(Number(card.preliminary_confidence) * 100)}%
          <span style={{ fontSize: "0.65rem", color: "#ccc" }}> (est.)</span>
        </span>
      </div>
    );
  }

  const wr = Math.round(card.win_rate * 100);
  const wrColor = wr >= 55 ? "#27ae60" : wr >= 45 ? "#e67e22" : "#c0392b";
  const ci = card.confidence_interval;

  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: "2px" }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: "0.25rem" }}>
        <span style={{ fontSize: "1.3rem", fontWeight: 700, color: wrColor }}>{wr}%</span>
        <span style={{ fontSize: "0.72rem", color: "#888" }}>{t("library.winRate")}</span>
      </div>
      {ci && (
        <span style={{ fontSize: "0.7rem", color: "#aaa" }}>
          {t("library.ci")}: [{Math.round(ci.lower * 100)}%–{Math.round(ci.upper * 100)}%] (n={ci.n})
        </span>
      )}
    </div>
  );
}

function WalkForwardBadge({ wf, t }: { wf: WalkForward | null; t: (k: string) => string }) {
  if (!wf || !wf.sufficient_data) return null;
  if (!wf.potentially_overfit) return null;
  return (
    <span style={badge("#e67e22")} title={t("library.overfitWarning")}>
      ⚠ {t("library.walkForward")}
    </span>
  );
}

function RunStatus({ run, t }: { run: AnalysisRun; t: (k: string, o?: Record<string, unknown>) => string }) {
  const kind = t(`library.run.${run.kind}`);
  const time = (iso: string | null) => (iso ? new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "");
  const scheduled = run.triggered_by_user_id === null ? ` ${t("library.run.scheduled")}` : "";
  const line: React.CSSProperties = { margin: 0, fontSize: "0.85rem", lineHeight: 1.5 };

  if (run.status === "running") {
    return <p style={{ ...line, color: "#1565c0" }}>⏳ {t("library.run.running", { kind, time: time(run.started_at) })}{scheduled}</p>;
  }
  const failures = run.failures.length > 0 && (
    <details style={{ fontSize: "0.8rem", color: "#666", marginTop: "0.2rem" }}>
      <summary style={{ cursor: "pointer" }}>{t("library.run.failedCount", { count: run.failures.length })}</summary>
      <ul style={{ margin: "0.3rem 0 0", paddingLeft: "1.2rem" }}>
        {run.failures.map((f, i) => <li key={i}><strong>{f.subject}</strong>: {f.reason}</li>)}
      </ul>
    </details>
  );
  if (run.status === "failed") {
    const didSomething = run.cards_created + run.cards_updated > 0;
    return (
      <div>
        <p style={{ ...line, color: "#c0392b" }}>
          ✕ {t("library.run.stopped", { kind, time: time(run.finished_at), reason: run.error_message ?? "" })}{scheduled}
        </p>
        {didSomething && (
          <p style={{ ...line, color: "#666" }}>
            {t("library.run.beforeStopping", { created: run.cards_created, updated: run.cards_updated })}
          </p>
        )}
        {failures}
      </div>
    );
  }
  const done = run.kind === "mining"
    ? t("library.run.miningDone", { time: time(run.finished_at), created: run.cards_created, updated: run.cards_updated })
    : t("library.run.validationDone", { time: time(run.finished_at), updated: run.cards_updated });
  return (
    <div>
      <p style={{ ...line, color: run.status === "partial" ? "#e67e22" : "#27ae60" }}>
        {run.status === "partial" ? "⚠" : "✓"} {done}{scheduled}
      </p>
      {failures}
    </div>
  );
}

export function StrategyLibrary() {
  const { t, i18n } = useTranslation();
  const { user } = useAuth();
  const workspaceId = user?.default_workspace_id;

  const [cards, setCards] = useState<StrategyCard[]>([]);
  const [planIds, setPlanIds] = useState<Set<string>>(new Set());
  const navigate = useNavigate();
  const [runs, setRuns] = useState<LatestRuns>({ mining: null, validation: null });
  const [starting, setStarting] = useState<RunKind | null>(null);
  const [runMsg, setRunMsg] = useState("");
  // What each kind's latest run was last time we looked, to notice one ending.
  const wasRunning = useRef<Record<RunKind, boolean>>({ mining: false, validation: false });
  const [filterSymbol, setFilterSymbol] = useState("");
  const [filterSetup, setFilterSetup] = useState("");

  const lang = i18n.language === "es" ? "es" : "en";

  const load = async () => {
    if (!workspaceId) return;
    const [cardsRes, planRes] = await Promise.all([
      api.get<StrategyCard[]>(`/workspaces/${workspaceId}/strategy-cards`),
      api.get<{ strategy_card_id: string }[]>(`/workspaces/${workspaceId}/plan-items`).catch(() => ({ data: [] })),
    ]);
    setCards(cardsRes.data);
    setPlanIds(new Set(planRes.data.map((p: { strategy_card_id: string }) => p.strategy_card_id)));
  };

  useEffect(() => { load(); }, [workspaceId]);

  const loadRuns = async (): Promise<LatestRuns | null> => {
    if (!workspaceId) return null;
    try {
      const { data } = await api.get<LatestRuns>(`/workspaces/${workspaceId}/runs/latest`);
      setRuns(data);
      return data;
    } catch {
      // A failed look must not stop the following: a new object re-arms the
      // poll below, which tries again.
      setRuns((prev) => ({ ...prev }));
      return null;
    }
  };

  useEffect(() => { loadRuns(); }, [workspaceId]);

  // Follow a running run until it ends, then refresh the cards it changed.
  useEffect(() => {
    const ended = (["mining", "validation"] as RunKind[]).some(
      (k) => wasRunning.current[k] && runs[k]?.status !== "running",
    );
    wasRunning.current = {
      mining: runs.mining?.status === "running",
      validation: runs.validation?.status === "running",
    };
    if (ended) load();
    if (!wasRunning.current.mining && !wasRunning.current.validation) return;
    const timer = setTimeout(loadRuns, RUN_POLL_MS);
    return () => clearTimeout(timer);
  }, [runs]);

  const startRun = async (kind: RunKind) => {
    if (!workspaceId) return;
    setStarting(kind); setRunMsg("");
    try {
      const path = kind === "mining" ? "mining/run" : "validation/run";
      const { data } = await api.post<AnalysisRun>(`/workspaces/${workspaceId}/${path}`);
      setRuns((prev) => ({ ...prev, [kind]: data }));
    } catch (e: unknown) {
      if ((e as { response?: { status?: number } }).response?.status === 409) {
        // One run at a time per workspace, of either kind: say which one is going.
        const latest = await loadRuns();
        const going = (["mining", "validation"] as RunKind[]).find((k) => latest?.[k]?.status === "running") ?? kind;
        setRunMsg(t("library.run.alreadyRunning", { kind: t(`library.run.${going}`) }));
      } else {
        setRunMsg(t("library.run.couldNotStart", { kind: t(`library.run.${kind}`) }));
      }
    } finally {
      setStarting(null);
    }
  };

  const addToPlan = async (cardId: string) => {
    if (!workspaceId) return;
    await api.post(`/workspaces/${workspaceId}/plan-items`, { strategy_card_id: cardId });
    setPlanIds((prev) => new Set([...prev, cardId]));
  };

  const filtered = cards.filter((c) => {
    const sym = filterSymbol.trim().toUpperCase();
    const st = filterSetup.trim().toLowerCase();
    return (!sym || c.symbol_scope.includes(sym)) && (!st || c.setup_type.toLowerCase().includes(st));
  });

  const inp: React.CSSProperties = {
    padding: "0.45rem 0.7rem", border: "1px solid #ddd", borderRadius: "4px",
    fontSize: "0.875rem", outline: "none",
  };
  const actionBtn = (disabled: boolean): React.CSSProperties => ({
    padding: "0.5rem 1.1rem", background: "#1a1a2e", color: "#fff", border: "none",
    borderRadius: "4px", cursor: "pointer", fontWeight: 600, fontSize: "0.875rem",
    opacity: disabled ? 0.65 : 1,
  });

  return (
    <div style={{ minHeight: "100vh", background: "#f5f5f5", fontFamily: "system-ui, sans-serif" }}>
      <NavBar />
      <main style={{ maxWidth: "940px", margin: "0 auto", padding: "2rem 1.5rem" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1.25rem" }}>
          <h1 style={{ margin: 0, fontSize: "1.4rem", color: "#1a1a2e" }}>{t("library.title")}</h1>
          <div style={{ display: "flex", gap: "0.5rem" }}>
            {(["mining", "validation"] as RunKind[]).map((kind) => {
              // Both wait while either runs: they write the same cards (#86).
              const busy = starting !== null || runs.mining?.status === "running" || runs.validation?.status === "running";
              return (
                <button key={kind} onClick={() => startRun(kind)} disabled={busy} style={actionBtn(busy)}>
                  {kind === "mining"
                    ? (runs.mining?.status === "running" ? t("library.mining") : t("library.runMining"))
                    : (runs.validation?.status === "running" ? t("library.validating") : t("library.runValidation"))}
                </button>
              );
            })}
          </div>
        </div>

        {(runs.mining || runs.validation || runMsg) && (
          <div style={{ display: "flex", flexDirection: "column", gap: "0.4rem", marginBottom: "1rem" }}>
            {runMsg && <p style={{ margin: 0, fontSize: "0.85rem", color: "#e67e22" }}>{runMsg}</p>}
            {runs.mining && <RunStatus run={runs.mining} t={t} />}
            {runs.validation && <RunStatus run={runs.validation} t={t} />}
          </div>
        )}

        <div style={{ display: "flex", gap: "0.6rem", marginBottom: "1.25rem", flexWrap: "wrap" }}>
          <input style={inp} placeholder={t("library.filterSymbol")} value={filterSymbol} onChange={(e) => setFilterSymbol(e.target.value)} />
          <input style={inp} placeholder={t("library.filterSetup")} value={filterSetup} onChange={(e) => setFilterSetup(e.target.value)} />
        </div>

        {filtered.length === 0 ? (
          <div style={{ ...sec, color: "#888", fontStyle: "italic" }}>{t("library.noCards")}</div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
            {filtered.map((card) => {
              const desc = lang === "es" ? card.description_es : card.description_en;
              return (
                <div key={card.id} style={sec}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "1rem" }}>
                    <div style={{ flex: 1 }}>
                      <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", flexWrap: "wrap", marginBottom: "0.4rem" }}>
                        {card.symbol_scope !== "general" && <span style={badge("#1a1a2e")}>{card.symbol_scope}</span>}
                        <span style={badge("#5c6bc0")}>{card.setup_type}</span>
                        {card.confidence_tier && (
                          <span style={badge(TIER_COLORS[card.confidence_tier] || "#888")}>
                            {t(`tier.${card.confidence_tier}`)}
                          </span>
                        )}
                        <WalkForwardBadge wf={card.walk_forward_result} t={t} />
                        {card.validation_updated_at && (
                          <span style={{ fontSize: "0.68rem", color: "#bbb" }}>
                            {t("library.validatedAt")} {new Date(card.validation_updated_at).toLocaleDateString()}
                          </span>
                        )}
                      </div>
                      <p style={{ margin: 0, fontSize: "0.9rem", color: "#333", lineHeight: 1.55 }}>{desc}</p>
                      <p style={{ margin: "0.3rem 0 0", fontSize: "0.75rem", color: "#aaa" }}>
                        {card.sample_size} {t("library.sampleSize")} · {card.source_count} {t("library.sources")}
                        {" · "}{t("library.version")}{card.version}
                      </p>
                    </div>

                    {/* Right column: stats + actions */}
                    <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: "0.6rem", minWidth: "160px" }}>
                      <WinRateDisplay card={card} t={t} />

                      <div style={{ display: "flex", gap: "0.35rem", flexWrap: "wrap", justifyContent: "flex-end" }}>
                        <button
                          onClick={() => navigate(`/library/${card.id}`)}
                          style={{ padding: "3px 10px", fontSize: "0.78rem", border: "1px solid #1565c0", borderRadius: "4px", background: "transparent", cursor: "pointer", color: "#1565c0" }}
                        >
                          {t("library.flowchart")}
                        </button>
                        {planIds.has(card.id) ? (
                          <span style={{ fontSize: "0.78rem", color: "#27ae60", padding: "3px 10px", border: "1px solid #27ae60", borderRadius: "4px" }}>
                            ✓ {t("library.inPlan")}
                          </span>
                        ) : (
                          <button
                            onClick={() => addToPlan(card.id)}
                            style={{ padding: "3px 10px", fontSize: "0.78rem", border: "none", borderRadius: "4px", background: "#1a1a2e", color: "#fff", cursor: "pointer" }}
                          >
                            {t("library.addToPlan")}
                          </button>
                        )}
                      </div>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </main>
    </div>
  );
}
