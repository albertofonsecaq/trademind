import { useState, FormEvent, useRef, useEffect } from "react";
import { useTranslation } from "react-i18next";
import { NavBar } from "../components/NavBar";
import { HelpLink } from "../components/HelpLink";
import { useAuth } from "../context/AuthContext";
import api from "../api/client";

type Source = {
  index: number;
  channel: string | null;
  author: string | null;
  timestamp: string | null;
  stable_id: string | null;
  symbol: string | null;
};

/** One row of the ask history: enough to recognise a question, not the answer. */
type AskRecordSummary = {
  id: string;
  question: string;
  created_at: string;
};

/** One ask record in full, as it was stored. */
type AskRecord = AskRecordSummary & {
  answer: string;
  sources: Source[];
  language: string;
};

/** One turn: the question that was asked, or the answer given for it. */
type Turn = {
  role: "user" | "assistant";
  text: string;
  sources?: Source[];
};

/** Which stored answer is open, and how far along reading it back has got. */
type Opened =
  | { status: "none" }
  | { status: "loading"; id: string }
  | { status: "ready"; id: string; record: AskRecord }
  | { status: "failed"; id: string };

/** How many rows of the ask history a page asks for. */
const HISTORY_LIMIT = 100;

/** An ask record reads as the question asked and the answer given, in that order. */
const asTurns = (record: AskRecord): Turn[] => [
  { role: "user", text: record.question },
  { role: "assistant", text: record.answer, sources: record.sources },
];

/**
 * One turn, drawn.
 *
 * A stored answer and one that has just arrived both come through here, which
 * is the point: reopening an ask record shows what was shown at the time, not a
 * second rendering of it that drifts away from the first.
 */
function Bubble({ turn, sourcesLabel }: { turn: Turn; sourcesLabel: string }) {
  const asked = turn.role === "user";
  return (
    <div style={{ alignSelf: asked ? "flex-end" : "flex-start", maxWidth: "85%" }}>
      <div style={{
        background: asked ? "#1a1a2e" : "#fff",
        color: asked ? "#fff" : "#222",
        borderRadius: "8px",
        padding: "0.75rem 1rem",
        border: asked ? "none" : "1px solid #e0e0e0",
        lineHeight: 1.6,
        whiteSpace: "pre-wrap",
        // A long answer, or one unbroken string inside it, wraps rather than
        // stretching the column past the window.
        overflowWrap: "anywhere",
      }}>
        {turn.text}
      </div>
      {!asked && turn.sources && turn.sources.length > 0 && (
        <div style={{ marginTop: "0.5rem", padding: "0.6rem 0.75rem", background: "#f9f9f9", border: "1px solid #e8e8e8", borderRadius: "6px" }}>
          <p style={{ fontSize: "0.75rem", fontWeight: 600, color: "#888", margin: "0 0 0.4rem" }}>
            {sourcesLabel}
          </p>
          {turn.sources.map((s) => (
            <div key={s.index} style={{ fontSize: "0.78rem", color: "#555", marginBottom: "0.2rem" }}>
              <strong>[{s.index}]</strong>{" "}
              {s.channel && <span>{s.channel}</span>}
              {s.author && <span> / {s.author}</span>}
              {s.symbol && <span> · <strong>{s.symbol}</strong></span>}
              {s.timestamp && <span style={{ color: "#999" }}> · {s.timestamp.slice(0, 10)}</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function Ask() {
  const { t, i18n } = useTranslation();
  const { user, loading: authLoading } = useAuth();
  const [query, setQuery] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [records, setRecords] = useState<AskRecordSummary[]>([]);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [historyError, setHistoryError] = useState(false);
  const [hasOlder, setHasOlder] = useState(false);
  const [olderLoading, setOlderLoading] = useState(false);
  const [opened, setOpened] = useState<Opened>({ status: "none" });
  const bottomRef = useRef<HTMLDivElement>(null);
  // Which record the user asked for last. A slower reply for one they have
  // since clicked away from must not land on top of the one they want.
  const wanted = useRef<string | null>(null);
  const workspaceId = user?.default_workspace_id;

  useEffect(() => {
    if (opened.status === "none") bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns, opened.status]);

  /** Read one stored answer back. A read: nothing here is synthesized or metered. */
  const openRecord = async (id: string) => {
    if (!workspaceId) return;
    wanted.current = id;
    setOpened({ status: "loading", id });
    try {
      const { data } = await api.get<AskRecord>(`/workspaces/${workspaceId}/ask-records/${id}`);
      if (wanted.current === id) setOpened({ status: "ready", id, record: data });
    } catch {
      if (wanted.current === id) setOpened({ status: "failed", id });
    }
  };

  const startNewQuestion = () => {
    wanted.current = null;                  // an open still in flight is no longer wanted
    setOpened({ status: "none" });
  };

  // Which questions were already asked. The answers stay in the database until
  // one of them is opened.
  useEffect(() => {
    if (authLoading) return;
    if (!workspaceId) { setHistoryLoading(false); return; }

    let cancelled = false;
    setHistoryLoading(true);
    api.get<AskRecordSummary[]>(`/workspaces/${workspaceId}/ask-records`, {
      params: { limit: HISTORY_LIMIT },
    })
      .then(({ data }) => {
        if (cancelled) return;
        setRecords(data);
        setHistoryError(false);
        setHasOlder(data.length >= HISTORY_LIMIT);
        // Open the newest straight away, so a reload lands on the last answer
        // rather than on an empty column beside a full list.
        if (data.length > 0) openRecord(data[0].id);
      })
      .catch(() => { if (!cancelled) setHistoryError(true); })
      .finally(() => { if (!cancelled) setHistoryLoading(false); });

    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceId, authLoading]);

  /** The page older than the oldest row on screen, anchored on that row. */
  const loadOlder = async () => {
    const oldest = records[records.length - 1];
    if (!workspaceId || !oldest) return;
    setOlderLoading(true);
    try {
      const { data } = await api.get<AskRecordSummary[]>(`/workspaces/${workspaceId}/ask-records`, {
        params: { limit: HISTORY_LIMIT, before_id: oldest.id },
      });
      setRecords((r) => [...r, ...data]);
      setHasOlder(data.length >= HISTORY_LIMIT);
    } catch {
      setHistoryError(true);
    } finally {
      setOlderLoading(false);
    }
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!query.trim()) { setError(t("ask.emptyQuery")); return; }
    setError("");

    const asked = query.trim();
    startNewQuestion();                     // a new question is not a stored one
    setTurns((turn) => [...turn, { role: "user", text: asked }]);
    setQuery("");
    setLoading(true);

    try {
      const { data } = await api.post(`/workspaces/${workspaceId}/ask`, {
        query: asked,
        language: i18n.language,
      });
      setTurns((turn) => [...turn, { role: "assistant", text: data.answer, sources: data.sources }]);
      // It belongs to the ask history the moment it is answered.
      setRecords((r) => [{ id: data.id, question: asked, created_at: data.created_at }, ...r]);
    } catch {
      setTurns((turn) => [...turn, { role: "assistant", text: t("ask.errorTitle"), sources: [] }]);
    } finally {
      setLoading(false);
    }
  };

  const askedOn = (iso: string) =>
    new Date(iso).toLocaleDateString(i18n.language, { year: "numeric", month: "short", day: "numeric" });

  const panel: React.CSSProperties = {
    width: "240px",
    flexShrink: 0,
    position: "sticky",
    top: "72px",
    alignSelf: "flex-start",
  };
  const panelRow = (open: boolean): React.CSSProperties => ({
    display: "block", width: "100%", textAlign: "left", cursor: "pointer",
    background: open ? "#1a1a2e" : "#fff",
    color: open ? "#fff" : "#333",
    border: "1px solid #e0e0e0", borderRadius: "6px",
    padding: "0.5rem 0.65rem", marginBottom: "0.4rem",
    fontSize: "0.8rem", lineHeight: 1.4, fontFamily: "inherit",
  });
  const quiet: React.CSSProperties = { color: "#999", fontSize: "0.8rem", margin: 0, lineHeight: 1.5 };

  const visibleTurns = opened.status === "ready" ? asTurns(opened.record) : turns;
  const openId = opened.status === "none" ? null : opened.id;

  return (
    <div style={{ minHeight: "100vh", background: "#f5f5f5", fontFamily: "system-ui, sans-serif", display: "flex", flexDirection: "column" }}>
      <NavBar />
      <main style={{ flex: 1, maxWidth: "1060px", width: "100%", margin: "0 auto", padding: "1.5rem", display: "flex", flexDirection: "column", gap: "1rem" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
          <h1 style={{ fontSize: "1.4rem", color: "#1a1a2e", margin: 0 }}>{t("ask.title")}</h1>
          <HelpLink section="ask" />
        </div>

        {error && <p style={{ color: "#c0392b", fontSize: "0.875rem" }}>{error}</p>}

        <div style={{ flex: 1, display: "flex", gap: "2rem", alignItems: "flex-start", flexWrap: "wrap" }}>
          {/* The ask history */}
          <aside style={panel}>
            <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginBottom: "0.5rem" }}>
              <h2 style={{ fontSize: "0.8rem", fontWeight: 600, color: "#888", margin: 0, textTransform: "uppercase", letterSpacing: "0.03em" }}>
                {t("ask.historyTitle")}
              </h2>
              {openId && (
                <button
                  onClick={startNewQuestion}
                  style={{ background: "none", border: "none", padding: 0, fontSize: "0.78rem", color: "#1a1a2e", cursor: "pointer", fontWeight: 600 }}
                >
                  {t("ask.newQuestion")}
                </button>
              )}
            </div>

            {historyLoading && (
              <p style={{ ...quiet, color: "#888", fontStyle: "italic" }}>{t("ask.loadingHistory")}</p>
            )}
            {historyError && (
              <p style={{ ...quiet, color: "#b9770e" }}>{t("ask.historyError")}</p>
            )}
            {!historyLoading && !historyError && records.length === 0 && (
              <p style={quiet}>{t("ask.historyEmpty")}</p>
            )}

            {records.map((r) => (
              <button key={r.id} onClick={() => openRecord(r.id)} style={panelRow(openId === r.id)}>
                <span style={{
                  display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical",
                  overflow: "hidden", overflowWrap: "anywhere",
                }}>
                  {r.question}
                </span>
                <span style={{ display: "block", marginTop: "0.25rem", fontSize: "0.7rem", color: openId === r.id ? "#bbb" : "#999" }}>
                  {askedOn(r.created_at)}
                </span>
              </button>
            ))}

            {hasOlder && (
              <button
                onClick={loadOlder}
                disabled={olderLoading}
                style={{
                  background: "none", border: "none", padding: "0.3rem 0", marginTop: "0.2rem",
                  fontSize: "0.78rem", color: "#1a1a2e", cursor: olderLoading ? "default" : "pointer",
                  fontWeight: 600, fontFamily: "inherit",
                }}
              >
                {olderLoading ? "..." : t("ask.loadOlder")}
              </button>
            )}
          </aside>

          {/* The exchange: a stored answer, or the one being asked now */}
          <div style={{ flex: 1, minWidth: "min(100%, 320px)", display: "flex", flexDirection: "column", gap: "1rem" }}>
            {opened.status === "loading" && (
              <p style={{ color: "#888", fontSize: "0.9rem", fontStyle: "italic", margin: 0 }}>{t("ask.opening")}</p>
            )}
            {opened.status === "failed" && (
              <p style={{ color: "#c0392b", fontSize: "0.875rem", margin: 0 }}>
                {t("ask.openError")}{" "}
                <button
                  onClick={() => openRecord(opened.id)}
                  style={{ background: "none", border: "none", padding: 0, color: "#1a1a2e", cursor: "pointer", fontWeight: 600, fontSize: "0.875rem", fontFamily: "inherit" }}
                >
                  {t("ask.openRetry")}
                </button>
              </p>
            )}

            {visibleTurns.map((turn, i) => (
              <Bubble key={i} turn={turn} sourcesLabel={t("ask.sources")} />
            ))}

            {loading && (
              <div style={{ alignSelf: "flex-start", color: "#888", fontStyle: "italic", fontSize: "0.9rem" }}>
                {t("ask.thinking")}
              </div>
            )}
            <div ref={bottomRef} />
          </div>
        </div>

        <form onSubmit={submit} style={{ display: "flex", gap: "0.5rem", marginTop: "auto" }}>
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t("ask.placeholder")}
            disabled={loading}
            style={{
              flex: 1,
              padding: "0.7rem 1rem",
              border: "1px solid #ddd",
              borderRadius: "6px",
              fontSize: "0.95rem",
              outline: "none",
            }}
          />
          <button
            type="submit"
            disabled={loading}
            style={{
              padding: "0.7rem 1.25rem",
              background: "#1a1a2e",
              color: "#fff",
              border: "none",
              borderRadius: "6px",
              cursor: "pointer",
              fontWeight: 600,
              fontSize: "0.9rem",
              opacity: loading ? 0.6 : 1,
            }}
          >
            {loading ? "..." : t("ask.submit")}
          </button>
        </form>
      </main>
    </div>
  );
}
