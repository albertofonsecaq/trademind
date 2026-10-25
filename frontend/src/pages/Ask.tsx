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

/** One question this user asked and the answer kept for it. */
type AskRecord = {
  id: string;
  question: string;
  answer: string;
  sources: Source[];
  language: string;
  created_at: string;
};

type Message = {
  role: "user" | "assistant";
  text: string;
  sources?: Source[];
  /** The ask record this came from, absent only while an answer is in flight. */
  recordId?: string;
};

/** A record reads as the two messages that produced it, in the order they happened. */
const asMessages = (record: AskRecord): Message[] => [
  { role: "user", text: record.question, recordId: record.id },
  { role: "assistant", text: record.answer, sources: record.sources, recordId: record.id },
];

export function Ask() {
  const { t, i18n } = useTranslation();
  const { user, loading: authLoading } = useAuth();
  const [query, setQuery] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [historyLoading, setHistoryLoading] = useState(true);
  const [historyError, setHistoryError] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const workspaceId = user?.default_workspace_id;

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // What was already asked and answered. Reading it back costs nothing — the
  // stored answer is served as it was, never synthesized again.
  useEffect(() => {
    if (authLoading) return;
    if (!workspaceId) { setHistoryLoading(false); return; }

    let cancelled = false;
    setHistoryLoading(true);
    api.get<AskRecord[]>(`/workspaces/${workspaceId}/ask-records`)
      .then(({ data }) => {
        if (cancelled) return;
        // Newest first from the server; a transcript reads oldest first. Put
        // them ahead of whatever is already on screen rather than replacing it,
        // so a question asked while this was in flight is not thrown away.
        const history = data.slice().reverse().flatMap(asMessages);
        setMessages((current) => [...history, ...current]);
        setHistoryError(false);
      })
      .catch(() => { if (!cancelled) setHistoryError(true); })
      .finally(() => { if (!cancelled) setHistoryLoading(false); });

    return () => { cancelled = true; };
  }, [workspaceId, authLoading]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!query.trim()) { setError(t("ask.emptyQuery")); return; }
    setError("");

    const asked = query.trim();
    setMessages((m) => [...m, { role: "user", text: asked }]);
    setQuery("");
    setLoading(true);

    try {
      const { data } = await api.post(`/workspaces/${workspaceId}/ask`, {
        query: asked,
        language: i18n.language,
      });
      setMessages((m) => [...m, {
        role: "assistant",
        text: data.answer,
        sources: data.sources,
        recordId: data.id,
      }]);
    } catch {
      setMessages((m) => [...m, { role: "assistant", text: t("ask.errorTitle"), sources: [] }]);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ minHeight: "100vh", background: "#f5f5f5", fontFamily: "system-ui, sans-serif", display: "flex", flexDirection: "column" }}>
      <NavBar />
      <main style={{ flex: 1, maxWidth: "760px", width: "100%", margin: "0 auto", padding: "1.5rem", display: "flex", flexDirection: "column", gap: "1rem" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
          <h1 style={{ fontSize: "1.4rem", color: "#1a1a2e", margin: 0 }}>{t("ask.title")}</h1>
          <HelpLink section="ask" />
        </div>

        {error && <p style={{ color: "#c0392b", fontSize: "0.875rem" }}>{error}</p>}

        <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: "1rem" }}>
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
          {messages.map((msg, i) => (
            <div key={i} style={{
              alignSelf: msg.role === "user" ? "flex-end" : "flex-start",
              maxWidth: "85%",
            }}>
              <div style={{
                background: msg.role === "user" ? "#1a1a2e" : "#fff",
                color: msg.role === "user" ? "#fff" : "#222",
                borderRadius: "8px",
                padding: "0.75rem 1rem",
                border: msg.role === "assistant" ? "1px solid #e0e0e0" : "none",
                lineHeight: 1.6,
                whiteSpace: "pre-wrap",
              }}>
                {msg.text}
              </div>
              {msg.role === "assistant" && msg.sources && msg.sources.length > 0 && (
                <div style={{ marginTop: "0.5rem", padding: "0.6rem 0.75rem", background: "#f9f9f9", border: "1px solid #e8e8e8", borderRadius: "6px" }}>
                  <p style={{ fontSize: "0.75rem", fontWeight: 600, color: "#888", margin: "0 0 0.4rem" }}>
                    {t("ask.sources")}
                  </p>
                  {msg.sources.map((s) => (
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
          ))}
          {loading && (
            <div style={{ alignSelf: "flex-start", color: "#888", fontStyle: "italic", fontSize: "0.9rem" }}>
              {t("ask.thinking")}
            </div>
          )}
          <div ref={bottomRef} />
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
