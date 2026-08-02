import { useState, useEffect, FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { NavBar } from "../components/NavBar";
import { HelpLink } from "../components/HelpLink";
import { useAuth } from "../context/AuthContext";
import api from "../api/client";

type Connection = { id: string; label: string; status: string; platform: string };
type Source = {
  id: string; source_type: string; identifier: string; fetch_cadence: string;
  content_filters: Record<string, boolean>; last_fetched_at: string | null;
  platform_connection_id: string | null;
};

const sec: React.CSSProperties = {
  background: "#fff", border: "1px solid #e0e0e0", borderRadius: "8px",
  padding: "1.25rem 1.5rem", marginBottom: "1.25rem",
};
const btn = (primary = true, danger = false): React.CSSProperties => ({
  padding: "0.5rem 1rem",
  background: primary ? "#1a1a2e" : "transparent",
  color: danger ? "#c0392b" : primary ? "#fff" : "#1a1a2e",
  border: primary ? "none" : `1px solid ${danger ? "#c0392b" : "#1a1a2e"}`,
  borderRadius: "4px", cursor: "pointer", fontSize: "0.85rem", fontWeight: 500,
});
const inp: React.CSSProperties = {
  padding: "0.5rem 0.7rem", border: "1px solid #ddd", borderRadius: "4px",
  fontSize: "0.9rem", width: "100%", boxSizing: "border-box",
};
const lbl: React.CSSProperties = { fontSize: "0.82rem", fontWeight: 500, color: "#555", marginBottom: "0.2rem", display: "block" };

const SOURCE_BADGE: Record<string, React.CSSProperties> = {
  telegram: { background: "#e3f2fd", color: "#1565c0" },
  youtube: { background: "#fce4ec", color: "#c62828" },
};

export function AdminSources() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const navigate = useNavigate();
  const workspaceId = user?.default_workspace_id;

  const [connections, setConnections] = useState<Connection[]>([]);
  const [sources, setSources] = useState<Source[]>([]);
  const [fetchingId, setFetchingId] = useState<string | null>(null);

  // Add source form state
  const [showAddSource, setShowAddSource] = useState(false);
  const [newSourceType, setNewSourceType] = useState<"telegram" | "youtube">("telegram");
  const [newIdentifier, setNewIdentifier] = useState("");
  const [newConnId, setNewConnId] = useState("");
  const [newCadence, setNewCadence] = useState("hourly");
  const [newFilters, setNewFilters] = useState({ text: true, image: false, video: false, url: false });
  const [addingSource, setAddingSource] = useState(false);
  const [addError, setAddError] = useState("");

  const load = async () => {
    if (!workspaceId) return;
    const [c, s] = await Promise.all([
      api.get<Connection[]>(`/workspaces/${workspaceId}/connections`),
      api.get<Source[]>(`/workspaces/${workspaceId}/sources`),
    ]);
    setConnections(c.data);
    setSources(s.data);
    if (c.data.length > 0 && !newConnId) setNewConnId(c.data[0].id);
  };

  useEffect(() => { load(); }, [workspaceId]);

  // When source type changes, reset form fields with sensible defaults per type
  const handleSourceTypeChange = (type: "telegram" | "youtube") => {
    setNewSourceType(type);
    setNewIdentifier("");
    setAddError("");
    setNewFilters(type === "youtube"
      ? { text: false, image: false, video: true, url: false }
      : { text: true, image: false, video: false, url: false }
    );
  };

  const addSource = async (e: FormEvent) => {
    e.preventDefault();
    if (!workspaceId) return;
    setAddError("");
    setAddingSource(true);

    const contentFilters = newFilters;

    try {
      await api.post(`/workspaces/${workspaceId}/sources`, {
        source_type: newSourceType,
        identifier: newIdentifier.trim(),
        platform_connection_id: newSourceType === "telegram" ? (newConnId || null) : null,
        fetch_cadence: newCadence,
        content_filters: contentFilters,
      });
      setNewIdentifier("");
      setShowAddSource(false);
      await load();
    } catch (err: unknown) {
      setAddError((err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? t("common.error"));
    } finally {
      setAddingSource(false);
    }
  };

  const fetchNow = async (sourceId: string) => {
    if (!workspaceId) return;
    setFetchingId(sourceId);
    try {
      await api.post(`/workspaces/${workspaceId}/sources/${sourceId}/fetch`);
    } finally {
      setFetchingId(null);
      setTimeout(load, 2000);
    }
  };

  const removeSource = async (sourceId: string) => {
    if (!workspaceId || !confirm(t("sources.removeSource") + "?")) return;
    await api.delete(`/workspaces/${workspaceId}/sources/${sourceId}`);
    await load();
  };

  const activeTelegramConns = connections.filter((c) => c.status === "active");

  return (
    <div style={{ minHeight: "100vh", background: "#f5f5f5", fontFamily: "system-ui, sans-serif" }}>
      <NavBar />
      <main style={{ maxWidth: "820px", margin: "0 auto", padding: "2rem 1.5rem" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginBottom: "1.5rem" }}>
          <h1 style={{ fontSize: "1.4rem", color: "#1a1a2e", margin: 0 }}>{t("sources.title")}</h1>
          <HelpLink section="sources" />
        </div>

        {/* Telegram Connections */}
        <div style={sec}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.75rem" }}>
            <h2 style={{ margin: 0, fontSize: "1rem" }}>{t("sources.connections")}</h2>
            <button style={btn()} onClick={() => navigate(`/sources/connect-telegram/${workspaceId}`)}>
              {t("sources.connectTelegram")}
            </button>
          </div>
          {connections.length === 0 ? (
            <p style={{ color: "#aaa", fontSize: "0.85rem", margin: 0 }}>—</p>
          ) : (
            connections.map((c) => (
              <div key={c.id} style={{ display: "flex", alignItems: "center", gap: "0.6rem", padding: "0.45rem 0", borderBottom: "1px solid #f5f5f5" }}>
                <span style={{ fontSize: "0.875rem", fontWeight: 500 }}>{c.label}</span>
                <span style={{ fontSize: "0.72rem", padding: "2px 8px", borderRadius: "12px", background: c.status === "active" ? "#e8f5e9" : "#fff3e0", color: c.status === "active" ? "#2e7d32" : "#e65100" }}>
                  {c.status}
                </span>
              </div>
            ))
          )}
        </div>

        {/* Sources list + Add */}
        <div style={sec}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.75rem" }}>
            <h2 style={{ margin: 0, fontSize: "1rem" }}>{t("sources.title")}</h2>
            <button style={btn()} onClick={() => setShowAddSource(!showAddSource)}>
              {t("sources.addSource")}
            </button>
          </div>

          {showAddSource && (
            <form onSubmit={addSource} style={{ background: "#f9f9f9", padding: "1rem", borderRadius: "6px", marginBottom: "1rem", display: "flex", flexDirection: "column", gap: "0.55rem" }}>
              {/* Source type selector */}
              <label style={lbl}>{t("sources.sourceType")}</label>
              <div style={{ display: "flex", gap: "0.5rem", marginBottom: "0.25rem" }}>
                {(["telegram", "youtube"] as const).map((type) => (
                  <button
                    key={type}
                    type="button"
                    onClick={() => handleSourceTypeChange(type)}
                    style={{
                      padding: "0.4rem 1rem", borderRadius: "4px", cursor: "pointer", fontSize: "0.85rem", fontWeight: 500,
                      background: newSourceType === type ? "#1a1a2e" : "transparent",
                      color: newSourceType === type ? "#fff" : "#1a1a2e",
                      border: "1px solid #1a1a2e",
                    }}
                  >
                    {t(`sources.${type}`)}
                  </button>
                ))}
              </div>

              {/* Identifier */}
              <label style={lbl}>{newSourceType === "youtube" ? t("sources.youtubeIdentifier") : t("sources.identifier")}</label>
              <input
                style={inp}
                value={newIdentifier}
                onChange={(e) => setNewIdentifier(e.target.value)}
                placeholder={newSourceType === "youtube" ? "@ChannelName or UCxxxxxxx" : "@channelname or -100123456789"}
                required
              />
              {newSourceType === "youtube" && (
                <p style={{ margin: 0, fontSize: "0.78rem", color: "#888" }}>{t("sources.youtubeNote")}</p>
              )}

              {/* Telegram connection selector */}
              {newSourceType === "telegram" && (
                <>
                  <label style={lbl}>{t("sources.connection")}</label>
                  {activeTelegramConns.length === 0 ? (
                    <p style={{ margin: 0, fontSize: "0.82rem", color: "#e65100" }}>
                      No active Telegram connections. Connect an account first.
                    </p>
                  ) : (
                    <select style={inp} value={newConnId} onChange={(e) => setNewConnId(e.target.value)}>
                      {activeTelegramConns.map((c) => (
                        <option key={c.id} value={c.id}>{c.label}</option>
                      ))}
                    </select>
                  )}
                </>
              )}

              {newSourceType === "youtube" && (
                <p style={{ margin: 0, fontSize: "0.82rem", color: "#555" }}>
                  {t("sources.noConnectionNeeded")}
                </p>
              )}

              {/* Cadence */}
              <label style={lbl}>{t("sources.cadence")}</label>
              <select style={inp} value={newCadence} onChange={(e) => setNewCadence(e.target.value)}>
                <option value="realtime">{t("sources.cadenceOptions.realtime")}</option>
                <option value="hourly">{t("sources.cadenceOptions.hourly")}</option>
                <option value="daily">{t("sources.cadenceOptions.daily")}</option>
              </select>

              {/* Content filter toggles */}
              <label style={lbl}>{t("sources.contentFilters")}</label>
              <p style={{ margin: "0 0 0.4rem", fontSize: "0.76rem", color: "#888" }}>{t("sources.contentFiltersNote")}</p>
              {(["text", "image", "video", "url"] as const).map((key) => (
                <label key={key} style={{ display: "flex", alignItems: "center", gap: "0.4rem", fontSize: "0.83rem", cursor: "pointer", marginBottom: "0.25rem" }}>
                  <input
                    type="checkbox"
                    checked={newFilters[key]}
                    onChange={(e) => setNewFilters((f) => ({ ...f, [key]: e.target.checked }))}
                  />
                  {t(`sources.${key}`)}
                </label>
              ))}

              {addError && <p style={{ margin: 0, color: "#c0392b", fontSize: "0.82rem" }}>{addError}</p>}

              <div style={{ display: "flex", gap: "0.5rem", marginTop: "0.25rem" }}>
                <button
                  type="submit"
                  style={btn()}
                  disabled={addingSource || (newSourceType === "telegram" && activeTelegramConns.length === 0)}
                >
                  {addingSource ? t("common.loading") : t("sources.addSource")}
                </button>
                <button type="button" style={btn(false)} onClick={() => { setShowAddSource(false); setAddError(""); }}>
                  {t("common.cancel")}
                </button>
              </div>
            </form>
          )}

          {sources.length === 0 ? (
            <p style={{ color: "#888", fontSize: "0.875rem", margin: 0 }}>{t("sources.noSources")}</p>
          ) : (
            sources.map((s) => (
              <div key={s.id} style={{ borderBottom: "1px solid #f0f0f0", padding: "0.75rem 0" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
                    <span style={{ fontWeight: 600, fontSize: "0.9rem" }}>{s.identifier}</span>
                    <span style={{ fontSize: "0.7rem", padding: "2px 7px", borderRadius: "10px", ...(SOURCE_BADGE[s.source_type] || {}) }}>
                      {s.source_type}
                    </span>
                    <span style={{ fontSize: "0.75rem", color: "#999" }}>{s.fetch_cadence}</span>
                  </div>
                  <div style={{ display: "flex", gap: "0.4rem" }}>
                    <button
                      style={btn()}
                      disabled={fetchingId === s.id}
                      onClick={() => fetchNow(s.id)}
                    >
                      {fetchingId === s.id ? t("sources.fetching") : t("sources.fetchNow")}
                    </button>
                    <button style={btn(false, true)} onClick={() => removeSource(s.id)}>
                      {t("common.delete")}
                    </button>
                  </div>
                </div>
                <div style={{ display: "flex", gap: "0.3rem", marginTop: "0.3rem", flexWrap: "wrap" }}>
                  {(["text", "image", "video", "url"] as const)
                    .filter((k) => s.content_filters?.[k])
                    .map((k) => (
                      <span key={k} style={{ fontSize: "0.7rem", padding: "1px 6px", background: "#f0f4ff", color: "#1a1a2e", borderRadius: "10px", border: "1px solid #d0d8ff" }}>
                        {k}
                      </span>
                    ))}
                </div>
                <p style={{ margin: "0.2rem 0 0", fontSize: "0.77rem", color: "#aaa" }}>
                  {t("sources.lastFetched")}: {s.last_fetched_at ? new Date(s.last_fetched_at).toLocaleString() : t("sources.never")}
                </p>
              </div>
            ))
          )}
        </div>
      </main>
    </div>
  );
}
