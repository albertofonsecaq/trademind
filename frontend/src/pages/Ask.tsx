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

type Message = {
  role: "user" | "assistant";
  text: string;
  sources?: Source[];
};

/** How much of the ask history this page asks for, and so how much it can list. */
const HISTORY_LIMIT = 100;

/** An ask record reads as the question asked and the answer given, in that order. */
const asMessages = (record: AskRecord): Message[] => [
  { role: "user", text: record.question },
  { role: "assistant", text: record.answer, sources: record.sources },
];

/**
 * One turn of the conversation.
 *
 * A stored answer and one that has just arrived go through here alike, which is
 * the point: reopening an ask record should show what was shown at the time,
 * not a second rendering of it that drifts.
 */
function Bubble({ message, sourcesLabel }: { message: Message; sourcesLabel: string }) {
  const mine = message.role === "user";
  return (
    <div style={{ alignSelf: mine ? "flex-end" : "flex-start", maxWidth: "85%" }}>
      <div style={{
        background: mine ? "#1a1a2e" : "#fff",
        color: mine ? "#fff" : "#222",
        borderRadius: "8px",
        padding: "0.75rem 1rem",
        border: mine ? "none" : "1px solid #e0e0e0",
        lineHeight: 1.6,
        whiteSpace: "pre-wrap",
        // A long answer, or one unbroken string in it, wraps rather than
        // stretching the column past the window.
        overflowWrap: "anywhere",
      }}>
        {message.text}
      </div>
      {!mine && message.sources && message.sources.length > 0 && (
        <div style={{ marginTop: "0.5rem", padding: "0.6rem 0.75rem", background: "#f9f9f9", border: "1px solid #e8e8e8", borderRadius: "6px" }}>
          <p style={{ fontSize: "0.75rem", fontWeight: 600, color: "#888", margin: "0 0 0.4rem" }}>
            {sourcesLabel}
          </p>
          {message.sources.map((s) => (
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
  const [messages, setMessages] = useState<Message[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [records, setRecords] = useState<AskRecordSummary[]>([]);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [historyError, setHistoryError] = useState(false);
  const [historyCapped, setHistoryCapped] = useState(false);
  // Which record is open, and the record itself once it has been read back.
  const [openId, setOpenId] = useState<string | null>(null);
  const [opened, setOpened] = useState<AskRecord | null>(null);
  const [openError, setOpenError] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const workspaceId = user?.default_workspace_id;

  useEffect(() => {
    if (!openId) bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, openId]);

  // Which questions were already asked. The answers stay in the database until
  // one is opened.
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
        // A full page means there are older records this is not listing. Say
        // so rather than stopping silently.
        setHistoryCapped(data.length >= HISTORY_LIMIT);
      })
      .catch(() => { if (!cancelled) setHistoryError(true); })
      .finally(() => { if (!cancelled) setHistoryLoading(false); });

    return () => { cancelled = true; };
  }, [workspaceId, authLoading]);

  /** Open a stored answer. A read: nothing here is synthesized or metered. */
  const open = async (id: string) => {
    setOpenId(id);
    setOpened(null);
    setOpenError(false);
    try {
      const { data } = await api.get<AskRecord>(`/workspaces/${workspaceId}/ask-records/${id}`);
      setOpened(data);
    } catch {
      setOpenError(true);
    }
  };

  const startNewQuestion = () => {
    setOpenId(null);
    setOpened(null);
    setOpenError(false);
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!query.trim()) { setError(t("ask.emptyQuery")); return; }
    setError("");

    const asked = query.trim();
    startNewQuestion();                     // a new question is not a stored one
    setMessages((m) => [...m, { role: "user", text: asked }]);
    setQuery("");
    setLoading(true);

    try {
      const { data } = await api.post(`/workspaces/${workspaceId}/ask`, {
        query: asked,
        language: i18n.language,
      });
      setMessages((m) => [...m, { role: "assistant", text: data.answer, sources: data.sources }]);
      // It is part of the history the moment it is answered.
      setRecords((r) => [{ id: data.id, question: asked, created_at: data.created_at }, ...r]);
    } catch {
      setMessages((m) => [...m, { role: "assistant", text: t("ask.errorTitle"), sources: [] }]);
    } finally {
      setLoading(false);
    }
  };

  const askedOn = (iso: string) =>
    new Date(iso).toLocaleDateString(i18n.language, { year: "numeric", month: "short", day: "numeric" });

  const shown = opened ? asMessages(opened) : messages;

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
          <aside style={{ width: "240px", flexShrink: 0, position: "sticky", top: "72px", alignSelf: "flex-start" }}>
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
              <p style={{ color: "#888", fontSize: "0.85rem", fontStyle: "italic", margin: 0 }}>
                {t("ask.loadingHistory")}
              </p>
            )}
            {historyError && (
              <p style={{ color: "#b9770e", fontSize: "0.85rem", margin: 0 }}>
                {t("ask.historyError")}
              </p>
            )}
            {!historyLoading && !historyError && records.length === 0 && (
              <p style={{ color: "#999", fontSize: "0.82rem", margin: 0, lineHeight: 1.5 }}>
                {t("ask.historyEmpty")}
              </p>
            )}

            {records.map((r) => (
              <button
                key={r.id}
                onClick={() => open(r.id)}
                style={{
                  display: "block", width: "100%", textAlign: "left", cursor: "pointer",
                  background: openId === r.id ? "#1a1a2e" : "#fff",
                  color: openId === r.id ? "#fff" : "#333",
                  border: "1px solid #e0e0e0", borderRadius: "6px",
                  padding: "0.5rem 0.65rem", marginBottom: "0.4rem",
                  fontSize: "0.8rem", lineHeight: 1.4, fontFamily: "inherit",
                }}
              >
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

            {historyCapped && (
              <p style={{ color: "#999", fontSize: "0.75rem", margin: "0.5rem 0 0" }}>
                {t("ask.historyCapped", { count: HISTORY_LIMIT })}
              </p>
            )}
          </aside>

          {/* The conversation: a stored answer, or this session's */}
          <div style={{ flex: 1, minWidth: "min(100%, 320px)", display: "flex", flexDirection: "column", gap: "1rem" }}>
            {openError && (
              <p style={{ color: "#c0392b", fontSize: "0.875rem", margin: 0 }}>{t("ask.openError")}</p>
            )}
            {openId && !opened && !openError && (
              <p style={{ color: "#888", fontSize: "0.9rem", fontStyle: "italic", margin: 0 }}>{t("ask.opening")}</p>
            )}

            {shown.map((msg, i) => (
              <Bubble key={i} message={msg} sourcesLabel={t("ask.sources")} />
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
