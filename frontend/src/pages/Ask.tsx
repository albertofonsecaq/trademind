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

type Message = {
  role: "user" | "assistant";
  text: string;
  sources?: Source[];
};

export function Ask() {
  const { t, i18n } = useTranslation();
  const { user } = useAuth();
  const [query, setQuery] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!query.trim()) { setError(t("ask.emptyQuery")); return; }
    setError("");

    const userMsg: Message = { role: "user", text: query };
    setMessages((m) => [...m, userMsg]);
    setQuery("");
    setLoading(true);

    try {
      const workspaceId = user?.default_workspace_id;
      const { data } = await api.post(`/workspaces/${workspaceId}/ask`, {
        query: query.trim(),
        language: i18n.language,
      });
      setMessages((m) => [...m, { role: "assistant", text: data.answer, sources: data.sources }]);
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
