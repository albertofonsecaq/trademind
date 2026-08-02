import { useState, useEffect, useCallback, FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { NavBar } from "../components/NavBar";
import { HelpLink } from "../components/HelpLink";
import { useAuth } from "../context/AuthContext";
import api from "../api/client";

type Connection = { id: string; label: string; status: string; platform: string };
type Source = {
  id: string; source_type: string; identifier: string; label: string | null;
  fetch_cadence: string; content_filters: Record<string, boolean>;
  last_fetched_at: string | null; platform_connection_id: string | null;
  backfill_start_date: string | null;
};
type BackfillJob = {
  id: string; status: string; items_ingested: number;
  date_range_start: string; date_range_end: string | null;
  created_at: string; completed_at: string | null; error_message: string | null;
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
const JOB_COLOR: Record<string, string> = {
  queued: "#888", running: "#1565c0", completed: "#2e7d32", failed: "#c0392b",
};

const PULSE_STYLE = `@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.3} }`;

// ── per-source config + backfill panel ────────────────────────────────────────
function SourcePanel({ source, workspaceId, onSaved }: {
  source: Source; workspaceId: string; onSaved: () => void;
}) {
  const { t } = useTranslation();
  const [tab, setTab] = useState<"settings" | "backfill">("settings");

  // settings state
  const [cadence, setCadence] = useState(source.fetch_cadence);
  const [filters, setFilters] = useState({ ...source.content_filters });
  const [startDate, setStartDate] = useState(
    source.backfill_start_date ? source.backfill_start_date.slice(0, 10) : ""
  );
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState("");

  // backfill state
  const [bfStart, setBfStart] = useState("");
  const [bfEnd, setBfEnd] = useState("");
  const [bfLoading, setBfLoading] = useState(false);
  const [jobs, setJobs] = useState<BackfillJob[]>([]);
  const [jobsLoading, setJobsLoading] = useState(false);

  const loadJobs = useCallback(async () => {
    setJobsLoading(true);
    try {
      const { data } = await api.get<BackfillJob[]>(
        `/workspaces/${workspaceId}/sources/${source.id}/backfill-jobs`
      );
      setJobs(data);
    } finally {
      setJobsLoading(false);
    }
  }, [workspaceId, source.id]);

  useEffect(() => { if (tab === "backfill") loadJobs(); }, [tab, loadJobs]);

  const saveSettings = async () => {
    setSaving(true); setSaveMsg("");
    try {
      await api.patch(`/workspaces/${workspaceId}/sources/${source.id}`, {
        fetch_cadence: cadence,
        content_filters: filters,
        backfill_start_date: startDate ? new Date(startDate).toISOString() : null,
      });
      setSaveMsg(t("common.saved"));
      onSaved();
    } catch {
      setSaveMsg(t("common.error"));
    } finally {
      setSaving(false);
    }
  };

  const triggerBackfill = async (e: FormEvent) => {
    e.preventDefault();
    if (!bfStart) return;
    setBfLoading(true);
    try {
      await api.post(`/workspaces/${workspaceId}/sources/${source.id}/backfill`, {
        date_range_start: new Date(bfStart).toISOString(),
        date_range_end: bfEnd ? new Date(bfEnd).toISOString() : null,
      });
      setBfStart(""); setBfEnd("");
      await loadJobs();
    } catch {
      // error shown inline
    } finally {
      setBfLoading(false);
    }
  };

  const tabBtn = (id: "settings" | "backfill"): React.CSSProperties => ({
    padding: "0.35rem 0.85rem", borderRadius: "4px", cursor: "pointer", fontSize: "0.82rem",
    fontWeight: 500, border: "none",
    background: tab === id ? "#1a1a2e" : "#f0f0f0",
    color: tab === id ? "#fff" : "#555",
  });

  return (
    <div style={{ background: "#fafafa", border: "1px solid #e8e8e8", borderRadius: "6px", padding: "1rem", marginTop: "0.5rem" }}>
      <div style={{ display: "flex", gap: "0.4rem", marginBottom: "0.9rem" }}>
        <button style={tabBtn("settings")} onClick={() => setTab("settings")}>{t("sources.settings")}</button>
        <button style={tabBtn("backfill")} onClick={() => setTab("backfill")}>{t("sources.backfill")}</button>
      </div>

      {tab === "settings" && (
        <div style={{ display: "flex", flexDirection: "column", gap: "0.55rem" }}>
          <label style={lbl}>{t("sources.cadence")}</label>
          <select style={{ ...inp, width: "auto" }} value={cadence} onChange={(e) => setCadence(e.target.value)}>
            <option value="realtime">{t("sources.cadenceOptions.realtime")}</option>
            <option value="hourly">{t("sources.cadenceOptions.hourly")}</option>
            <option value="daily">{t("sources.cadenceOptions.daily")}</option>
          </select>

          <label style={lbl}>{t("sources.contentFilters")}</label>
          {(["text", "image", "video", "url"] as const).map((key) => (
            <label key={key} style={{ display: "flex", alignItems: "center", gap: "0.4rem", fontSize: "0.83rem", cursor: "pointer" }}>
              <input type="checkbox" checked={!!filters[key]}
                onChange={(e) => setFilters((f) => ({ ...f, [key]: e.target.checked }))} />
              {t(`sources.${key}`)}
            </label>
          ))}

          <label style={{ ...lbl, marginTop: "0.4rem" }}>{t("sources.backfillStartDate")}</label>
          <input type="date" style={{ ...inp, width: "auto" }} value={startDate}
            onChange={(e) => setStartDate(e.target.value)} />
          <p style={{ margin: 0, fontSize: "0.76rem", color: "#888" }}>{t("sources.backfillStartDateHint")}</p>

          <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginTop: "0.25rem" }}>
            <button style={btn()} onClick={saveSettings} disabled={saving}>
              {saving ? t("common.loading") : t("common.save")}
            </button>
            {saveMsg && <span style={{ fontSize: "0.82rem", color: saveMsg === t("common.saved") ? "#27ae60" : "#c0392b" }}>{saveMsg}</span>}
          </div>
        </div>
      )}

      {tab === "backfill" && (
        <div>
          <p style={{ fontSize: "0.82rem", color: "#555", marginTop: 0 }}>{t("sources.backfillHint")}</p>
          <form onSubmit={triggerBackfill} style={{ display: "flex", flexWrap: "wrap", gap: "0.5rem", alignItems: "flex-end", marginBottom: "1rem" }}>
            <div>
              <label style={lbl}>{t("sources.backfillFrom")} *</label>
              <input type="date" style={{ ...inp, width: "160px" }} value={bfStart}
                onChange={(e) => setBfStart(e.target.value)} required />
            </div>
            <div>
              <label style={lbl}>{t("sources.backfillTo")}</label>
              <input type="date" style={{ ...inp, width: "160px" }} value={bfEnd}
                onChange={(e) => setBfEnd(e.target.value)} />
            </div>
            <button type="submit" style={btn()} disabled={bfLoading || !bfStart}>
              {bfLoading ? t("common.loading") : t("sources.triggerBackfill")}
            </button>
          </form>

          <h4 style={{ fontSize: "0.82rem", color: "#555", margin: "0 0 0.5rem", fontWeight: 600 }}>{t("sources.backfillJobs")}</h4>
          {jobsLoading ? (
            <p style={{ fontSize: "0.82rem", color: "#aaa" }}>{t("common.loading")}</p>
          ) : jobs.length === 0 ? (
            <p style={{ fontSize: "0.82rem", color: "#aaa" }}>{t("sources.noJobs")}</p>
          ) : (
            jobs.map((job) => (
              <div key={job.id} style={{ display: "flex", gap: "0.6rem", alignItems: "center", padding: "0.4rem 0", borderBottom: "1px solid #f0f0f0", fontSize: "0.8rem" }}>
                <span style={{ fontWeight: 700, color: JOB_COLOR[job.status] || "#888", minWidth: "70px" }}>{job.status}</span>
                <span style={{ color: "#555" }}>
                  {job.date_range_start.slice(0, 10)} → {job.date_range_end ? job.date_range_end.slice(0, 10) : t("sources.now")}
                </span>
                <span style={{ color: "#27ae60" }}>{job.items_ingested} {t("sources.items")}</span>
                {job.error_message && <span style={{ color: "#c0392b" }}>{job.error_message}</span>}
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}

// ── main page ─────────────────────────────────────────────────────────────────
export function AdminSources() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const navigate = useNavigate();
  const workspaceId = user?.default_workspace_id;

  const [connections, setConnections] = useState<Connection[]>([]);
  const [sources, setSources] = useState<Source[]>([]);
  const [fetchingId, setFetchingId] = useState<string | null>(null);
  const [activeFetches, setActiveFetches] = useState<Record<string, string>>({}); // id → triggered-at ISO
  const [expandedId, setExpandedId] = useState<string | null>(null);

  // Add source form
  const [showAddSource, setShowAddSource] = useState(false);
  const [newSourceType, setNewSourceType] = useState<"telegram" | "youtube">("telegram");
  const [newIdentifier, setNewIdentifier] = useState("");
  const [newConnId, setNewConnId] = useState("");
  const [newCadence, setNewCadence] = useState("hourly");
  const [newFilters, setNewFilters] = useState({ text: true, image: false, video: false, url: false });
  const [newBackfillDate, setNewBackfillDate] = useState("");
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
    setAddError(""); setAddingSource(true);
    try {
      await api.post(`/workspaces/${workspaceId}/sources`, {
        source_type: newSourceType,
        identifier: newIdentifier.trim(),
        platform_connection_id: newSourceType === "telegram" ? (newConnId || null) : null,
        fetch_cadence: newCadence,
        content_filters: newFilters,
        backfill_start_date: newBackfillDate ? new Date(newBackfillDate).toISOString() : null,
      });
      setNewIdentifier(""); setNewBackfillDate(""); setShowAddSource(false);
      await load();
    } catch (err: unknown) {
      setAddError((err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? t("common.error"));
    } finally {
      setAddingSource(false);
    }
  };

  // Poll sources every 5s while any fetch is active; clear when last_fetched_at updates
  useEffect(() => {
    if (Object.keys(activeFetches).length === 0) return;
    const interval = setInterval(async () => {
      if (!workspaceId) return;
      const { data } = await api.get<Source[]>(`/workspaces/${workspaceId}/sources`);
      setSources(data);
      setActiveFetches((prev) => {
        const next = { ...prev };
        for (const [id, triggeredAt] of Object.entries(prev)) {
          const updated = data.find((s) => s.id === id);
          if (updated?.last_fetched_at && updated.last_fetched_at > triggeredAt) {
            delete next[id];
          }
        }
        return next;
      });
    }, 5000);
    return () => clearInterval(interval);
  }, [activeFetches, workspaceId]);

  const fetchNow = async (sourceId: string) => {
    if (!workspaceId) return;
    setFetchingId(sourceId);
    try {
      await api.post(`/workspaces/${workspaceId}/sources/${sourceId}/fetch`);
      setActiveFetches((prev) => ({ ...prev, [sourceId]: new Date().toISOString() }));
    } finally {
      setFetchingId(null);
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
      <style>{PULSE_STYLE}</style>
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

        {/* Sources list */}
        <div style={sec}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.75rem" }}>
            <h2 style={{ margin: 0, fontSize: "1rem" }}>{t("sources.title")}</h2>
            <button style={btn()} onClick={() => setShowAddSource(!showAddSource)}>{t("sources.addSource")}</button>
          </div>

          {/* Add source form */}
          {showAddSource && (
            <form onSubmit={addSource} style={{ background: "#f9f9f9", padding: "1rem", borderRadius: "6px", marginBottom: "1rem", display: "flex", flexDirection: "column", gap: "0.55rem" }}>
              <label style={lbl}>{t("sources.sourceType")}</label>
              <div style={{ display: "flex", gap: "0.5rem", marginBottom: "0.25rem" }}>
                {(["telegram", "youtube"] as const).map((type) => (
                  <button key={type} type="button" onClick={() => handleSourceTypeChange(type)}
                    style={{ padding: "0.4rem 1rem", borderRadius: "4px", cursor: "pointer", fontSize: "0.85rem", fontWeight: 500, border: "1px solid #1a1a2e", background: newSourceType === type ? "#1a1a2e" : "transparent", color: newSourceType === type ? "#fff" : "#1a1a2e" }}>
                    {t(`sources.${type}`)}
                  </button>
                ))}
              </div>

              <label style={lbl}>{newSourceType === "youtube" ? t("sources.youtubeIdentifier") : t("sources.identifier")}</label>
              <input style={inp} value={newIdentifier} onChange={(e) => setNewIdentifier(e.target.value)}
                placeholder={newSourceType === "youtube" ? "@ChannelName or UCxxxxxxx" : "@channelname or -100123456789"} required />
              {newSourceType === "youtube" && <p style={{ margin: 0, fontSize: "0.78rem", color: "#888" }}>{t("sources.youtubeNote")}</p>}

              {newSourceType === "telegram" && (
                <>
                  <label style={lbl}>{t("sources.connection")}</label>
                  {activeTelegramConns.length === 0 ? (
                    <p style={{ margin: 0, fontSize: "0.82rem", color: "#e65100" }}>No active Telegram connections. Connect an account first.</p>
                  ) : (
                    <select style={inp} value={newConnId} onChange={(e) => setNewConnId(e.target.value)}>
                      {activeTelegramConns.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}
                    </select>
                  )}
                </>
              )}
              {newSourceType === "youtube" && <p style={{ margin: 0, fontSize: "0.82rem", color: "#555" }}>{t("sources.noConnectionNeeded")}</p>}

              <label style={lbl}>{t("sources.cadence")}</label>
              <select style={inp} value={newCadence} onChange={(e) => setNewCadence(e.target.value)}>
                <option value="realtime">{t("sources.cadenceOptions.realtime")}</option>
                <option value="hourly">{t("sources.cadenceOptions.hourly")}</option>
                <option value="daily">{t("sources.cadenceOptions.daily")}</option>
              </select>

              <label style={lbl}>{t("sources.contentFilters")}</label>
              <p style={{ margin: "0 0 0.4rem", fontSize: "0.76rem", color: "#888" }}>{t("sources.contentFiltersNote")}</p>
              {(["text", "image", "video", "url"] as const).map((key) => (
                <label key={key} style={{ display: "flex", alignItems: "center", gap: "0.4rem", fontSize: "0.83rem", cursor: "pointer", marginBottom: "0.25rem" }}>
                  <input type="checkbox" checked={newFilters[key]} onChange={(e) => setNewFilters((f) => ({ ...f, [key]: e.target.checked }))} />
                  {t(`sources.${key}`)}
                </label>
              ))}

              <label style={{ ...lbl, marginTop: "0.25rem" }}>{t("sources.backfillStartDate")}</label>
              <input type="date" style={{ ...inp, width: "auto" }} value={newBackfillDate} onChange={(e) => setNewBackfillDate(e.target.value)} />
              <p style={{ margin: 0, fontSize: "0.76rem", color: "#888" }}>{t("sources.backfillStartDateHint")}</p>

              {addError && <p style={{ margin: 0, color: "#c0392b", fontSize: "0.82rem" }}>{addError}</p>}
              <div style={{ display: "flex", gap: "0.5rem", marginTop: "0.25rem" }}>
                <button type="submit" style={btn()} disabled={addingSource || (newSourceType === "telegram" && activeTelegramConns.length === 0)}>
                  {addingSource ? t("common.loading") : t("sources.addSource")}
                </button>
                <button type="button" style={btn(false)} onClick={() => { setShowAddSource(false); setAddError(""); }}>
                  {t("common.cancel")}
                </button>
              </div>
            </form>
          )}

          {/* Source rows */}
          {sources.length === 0 ? (
            <p style={{ color: "#888", fontSize: "0.875rem", margin: 0 }}>{t("sources.noSources")}</p>
          ) : (
            sources.map((s) => (
              <div key={s.id} style={{ borderBottom: "1px solid #f0f0f0", padding: "0.75rem 0" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", flexWrap: "wrap" }}>
                    <span style={{ fontWeight: 600, fontSize: "0.9rem" }}>{s.label || s.identifier}</span>
                    {s.label && <span style={{ fontSize: "0.75rem", color: "#aaa", fontFamily: "monospace" }}>{s.identifier}</span>}
                    <span style={{ fontSize: "0.7rem", padding: "2px 7px", borderRadius: "10px", ...(SOURCE_BADGE[s.source_type] || {}) }}>{s.source_type}</span>
                    <span style={{ fontSize: "0.75rem", color: "#999" }}>{s.fetch_cadence}</span>
                  </div>
                  <div style={{ display: "flex", gap: "0.4rem" }}>
                    <button style={btn(false)} onClick={() => setExpandedId(expandedId === s.id ? null : s.id)}>
                      {expandedId === s.id ? t("common.collapse") : t("sources.configure")}
                    </button>
                    <button style={btn()} disabled={fetchingId === s.id} onClick={() => fetchNow(s.id)}>
                      {fetchingId === s.id ? t("sources.fetching") : t("sources.fetchNow")}
                    </button>
                    <button style={btn(false, true)} onClick={() => removeSource(s.id)}>{t("common.delete")}</button>
                  </div>
                </div>

                <div style={{ display: "flex", gap: "0.3rem", marginTop: "0.3rem", flexWrap: "wrap" }}>
                  {(["text", "image", "video", "url"] as const).filter((k) => s.content_filters?.[k]).map((k) => (
                    <span key={k} style={{ fontSize: "0.7rem", padding: "1px 6px", background: "#f0f4ff", color: "#1a1a2e", borderRadius: "10px", border: "1px solid #d0d8ff" }}>{k}</span>
                  ))}
                  {s.backfill_start_date && (
                    <span style={{ fontSize: "0.7rem", padding: "1px 6px", background: "#fff8e1", color: "#f57f17", borderRadius: "10px", border: "1px solid #ffe082" }}>
                      {t("sources.from")} {s.backfill_start_date.slice(0, 10)}
                    </span>
                  )}
                </div>
                <p style={{ margin: "0.2rem 0 0", fontSize: "0.77rem", color: "#aaa" }}>
                  {t("sources.lastFetched")}: {s.last_fetched_at ? new Date(s.last_fetched_at).toLocaleString() : t("sources.never")}
                </p>
                {activeFetches[s.id] && (
                  <p style={{ margin: "0.15rem 0 0", fontSize: "0.77rem", color: "#1565c0", display: "flex", alignItems: "center", gap: "0.3rem" }}>
                    <span style={{ display: "inline-block", width: "8px", height: "8px", borderRadius: "50%", background: "#1565c0", animation: "pulse 1.2s infinite" }} />
                    {t("sources.fetchInProgress")}
                  </p>
                )}

                {expandedId === s.id && workspaceId && (
                  <SourcePanel source={s} workspaceId={workspaceId} onSaved={load} />
                )}
              </div>
            ))
          )}
        </div>
      </main>
    </div>
  );
}
