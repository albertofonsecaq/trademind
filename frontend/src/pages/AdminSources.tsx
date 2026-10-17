import { useState, useEffect, useCallback, FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { NavBar } from "../components/NavBar";
import { HelpLink } from "../components/HelpLink";
import { useAuth } from "../context/AuthContext";
import api, { type Workspace } from "../api/client";

type Connection = { id: string; label: string; status: string; platform: string };
type Source = {
  id: string; source_type: string; identifier: string; label: string | null;
  fetch_cadence: string; content_filters: Record<string, boolean>;
  last_fetched_at: string | null; platform_connection_id: string | null;
  backfill_start_date: string | null; is_paused: boolean; paused_at: string | null;
};
type ResumeMode = "catch_up" | "from_now";
type BackfillJob = {
  id: string; status: string; items_ingested: number; items_failed: number;
  date_range_start: string; date_range_end: string | null;
  created_at: string; completed_at: string | null; error_message: string | null;
};

const sec: React.CSSProperties = {
  background: "#fff", border: "1px solid #e0e0e0", borderRadius: "8px",
  padding: "1.25rem 1.5rem", marginBottom: "1.25rem",
};
const btn = (primary = true, danger = false, disabled = false): React.CSSProperties => ({
  padding: "0.5rem 1rem",
  background: primary ? "#1a1a2e" : "transparent",
  color: danger ? "#c0392b" : primary ? "#fff" : "#1a1a2e",
  border: primary ? "none" : `1px solid ${danger ? "#c0392b" : "#1a1a2e"}`,
  borderRadius: "4px", cursor: "pointer", fontSize: "0.85rem", fontWeight: 500,
  opacity: disabled ? 0.65 : 1,
});
const inp: React.CSSProperties = {
  padding: "0.5rem 0.7rem", border: "1px solid #ddd", borderRadius: "4px",
  fontSize: "0.9rem", width: "100%", boxSizing: "border-box",
};
const lbl: React.CSSProperties = { fontSize: "0.82rem", fontWeight: 500, color: "#555", marginBottom: "0.2rem", display: "block" };

const PAUSED_COLOR = "#f57f17";
const pill: React.CSSProperties = { fontSize: "0.7rem", padding: "2px 7px", borderRadius: "10px" };

const SOURCE_BADGE: Record<string, React.CSSProperties> = {
  telegram: { background: "#e3f2fd", color: "#1565c0" },
  youtube: { background: "#fce4ec", color: "#c62828" },
};
const JOB_COLOR: Record<string, string> = {
  queued: "#888", running: "#1565c0", completed: "#2e7d32", failed: "#c0392b",
  cancelled: PAUSED_COLOR, partial: "#e67e22",
};

const PULSE_STYLE = `@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.3} }`;

// ── pause controls, shared by the source row and its config panel ────────────
function PausedBadge() {
  const { t } = useTranslation();
  return (
    <span style={{ ...pill, background: "#fff8e1", color: PAUSED_COLOR, border: "1px solid #ffe082", fontWeight: 600 }}>
      {t("sources.paused")}
    </span>
  );
}

function PausedNotes({ source, error, workspacePaused }: {
  source: Source; error?: string; workspacePaused?: boolean;
}) {
  const { t } = useTranslation();
  if (!source.is_paused && !error && !workspacePaused) return null;
  return (
    <>
      {error && (
        <p style={{ margin: "0.15rem 0 0", fontSize: "0.77rem", color: "#c0392b" }}>{error}</p>
      )}
      {source.is_paused && (
        <p style={{ margin: "0.15rem 0 0", fontSize: "0.77rem", color: PAUSED_COLOR }}>
          {t("sources.pausedHint")}
        </p>
      )}
      {!source.is_paused && workspacePaused && (
        <p style={{ margin: "0.15rem 0 0", fontSize: "0.77rem", color: PAUSED_COLOR }}>
          {t("sources.workspacePausedNoFetch")}
        </p>
      )}
    </>
  );
}

function ResumeDialog({ source, busy, onChoose, onCancel }: {
  source: Source; busy: boolean;
  onChoose: (mode: ResumeMode) => void; onCancel: () => void;
}) {
  const { t } = useTranslation();
  const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : null);

  // The choice is only informed if the cost is legible, so say how much backlog
  // is waiting rather than printing two timestamps and leaving the subtraction
  // to the reader.
  const span = (iso: string | null): string | null => {
    if (!iso) return null;
    const minutes = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
    if (minutes >= 1440) return t("sources.resumeSpanDays", { count: Math.round(minutes / 1440) });
    if (minutes >= 60) return t("sources.resumeSpanHours", { count: Math.round(minutes / 60) });
    return t("sources.resumeSpanMinutes", { count: minutes });
  };

  const pausedSince = when(source.paused_at);
  const pausedSpan = span(source.paused_at);
  const fetchedSince = when(source.last_fetched_at);
  const fetchedSpan = span(source.last_fetched_at);

  const choice = (
    mode: ResumeMode, primary: boolean, label: string, hint: string,
  ) => (
    <div style={{ marginTop: "0.6rem" }}>
      <button style={btn(primary, false, busy)} disabled={busy} onClick={() => onChoose(mode)}>
        {label}
      </button>
      <p style={{ margin: "0.25rem 0 0", fontSize: "0.77rem", color: "#666" }}>{hint}</p>
    </div>
  );

  return (
    <div style={{ background: "#fff8e1", border: "1px solid #ffe082", borderRadius: "6px", padding: "0.9rem", marginTop: "0.5rem" }}>
      <h4 style={{ margin: "0 0 0.35rem", fontSize: "0.9rem", color: "#1a1a2e" }}>{t("sources.resumeTitle")}</h4>
      {/* The backlog is what makes the choice informed: an hour paused and three
          weeks paused cost very different amounts to catch up on. */}
      <p style={{ margin: 0, fontSize: "0.8rem", color: "#555" }}>
        {pausedSince && pausedSpan
          ? t("sources.resumePausedSince", { span: pausedSpan, since: pausedSince })
          : t("sources.resumePausedUnknown")}
      </p>
      <p style={{ margin: "0.15rem 0 0", fontSize: "0.8rem", color: "#555" }}>
        {fetchedSince && fetchedSpan
          ? t("sources.resumeBacklog", { span: fetchedSpan, since: fetchedSince })
          : t("sources.resumeBacklogNever")}
      </p>
      {choice("catch_up", true, t("sources.resumeCatchUp"), t("sources.resumeCatchUpHint"))}
      {choice("from_now", false, t("sources.resumeFromNow"), t("sources.resumeFromNowHint"))}
      <button style={{ ...btn(false), marginTop: "0.7rem" }} disabled={busy} onClick={onCancel}>
        {t("common.cancel")}
      </button>
    </div>
  );
}

function PauseButton({ source, busy, onToggle }: {
  source: Source; busy: boolean; onToggle: (source: Source) => void;
}) {
  const { t } = useTranslation();
  return (
    <button style={btn(false, false, busy)} disabled={busy} onClick={() => onToggle(source)}>
      {busy ? t("common.loading") : source.is_paused ? t("sources.resume") : t("sources.pause")}
    </button>
  );
}

function FetchButton({ source, busy, blocked, onFetch }: {
  source: Source; busy: boolean; blocked: boolean; onFetch: (id: string) => void;
}) {
  const { t } = useTranslation();
  const off = busy || source.is_paused || blocked;
  return (
    <button style={btn(true, false, off)} disabled={off} onClick={() => onFetch(source.id)}>
      {busy ? t("sources.fetching") : t("sources.fetchNow")}
    </button>
  );
}

// ── workspace-wide ingestion switch ──────────────────────────────────────────
function WorkspaceIngestion({ workspace, busy, error, onToggle }: {
  workspace: Workspace; busy: boolean; error?: string; onToggle: (paused: boolean) => void;
}) {
  const { t } = useTranslation();
  const paused = workspace.ingestion_paused;
  return (
    <div style={{ ...sec, ...(paused ? { background: "#fff8e1", borderColor: "#ffe082" } : {}) }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "1rem" }}>
        <div>
          <h2 style={{ margin: 0, fontSize: "1rem" }}>
            {t("sources.workspacePauseTitle")}
            {paused && <span style={{ marginLeft: "0.5rem" }}><PausedBadge /></span>}
          </h2>
          {/* Says what the pause does and does not cost, so nobody has to guess
              whether switching it on loses the knowledge base. */}
          <p style={{ margin: "0.3rem 0 0", fontSize: "0.8rem", color: paused ? PAUSED_COLOR : "#777", maxWidth: "52rem" }}>
            {paused ? t("sources.workspacePausePaused") : t("sources.workspacePauseRunning")}
          </p>
          {error && <p style={{ margin: "0.3rem 0 0", fontSize: "0.8rem", color: "#c0392b" }}>{error}</p>}
        </div>
        <button
          style={{ ...btn(!paused, false, busy), whiteSpace: "nowrap" }}
          disabled={busy}
          onClick={() => onToggle(!paused)}
        >
          {busy ? t("common.loading") : paused ? t("sources.workspaceResume") : t("sources.workspacePause")}
        </button>
      </div>
    </div>
  );
}

// ── per-source config + backfill panel ────────────────────────────────────────
function SourcePanel({ source, workspaceId, workspacePaused, onSaved }: {
  source: Source; workspaceId: string; workspacePaused: boolean; onSaved: () => void;
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

  const backfillOff = source.is_paused || workspacePaused;

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
          {source.is_paused && (
            <p style={{ fontSize: "0.82rem", color: PAUSED_COLOR, marginTop: 0 }}>{t("sources.pausedNoBackfill")}</p>
          )}
          {!source.is_paused && workspacePaused && (
            <p style={{ fontSize: "0.82rem", color: PAUSED_COLOR, marginTop: 0 }}>{t("sources.workspacePausedNoFetch")}</p>
          )}
          <form onSubmit={triggerBackfill} style={{ display: "flex", flexWrap: "wrap", gap: "0.5rem", alignItems: "flex-end", marginBottom: "1rem" }}>
            <div>
              <label style={lbl}>{t("sources.backfillFrom")} *</label>
              <input type="date" style={{ ...inp, width: "160px" }} value={bfStart}
                onChange={(e) => setBfStart(e.target.value)} disabled={backfillOff} required />
            </div>
            <div>
              <label style={lbl}>{t("sources.backfillTo")}</label>
              <input type="date" style={{ ...inp, width: "160px" }} value={bfEnd}
                onChange={(e) => setBfEnd(e.target.value)} disabled={backfillOff} />
            </div>
            <button
              type="submit"
              style={btn(true, false, backfillOff || bfLoading || !bfStart)}
              disabled={backfillOff || bfLoading || !bfStart}
            >
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
                <span style={{ fontWeight: 700, color: JOB_COLOR[job.status] || "#888", minWidth: "70px" }}>
                  {t(`sources.jobStatus.${job.status}`, job.status)}
                </span>
                <span style={{ color: "#555" }}>
                  {job.date_range_start.slice(0, 10)} → {job.date_range_end ? job.date_range_end.slice(0, 10) : t("sources.now")}
                </span>
                <span style={{ color: "#27ae60" }}>{job.items_ingested} {t("sources.items")}</span>
                {job.items_failed > 0 && (
                  <span style={{ color: "#c0392b" }}>
                    {t("sources.itemsFailed", { count: job.items_failed })}
                  </span>
                )}
                {job.error_message && <span style={{ color: "#c0392b" }}>{job.error_message}</span>}
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}

// ── one source, one row ───────────────────────────────────────────────────────
// Every per-source affordance lives here. Two lists used to render their own
// copy, so a YouTube channel appeared twice with two sets of controls — and two
// resume prompts at once — and anything new had to be written into both (#18).
function SourceRow({
  source, workspaceId, workspacePaused,
  expanded, busy, fetching, fetchInProgress, resumePromptOpen, pauseError,
  onToggleExpanded, onTogglePause, onFetch, onResume, onCancelResume, onDelete, onSaved,
}: {
  source: Source;
  workspaceId: string | null | undefined;
  workspacePaused: boolean;
  expanded: boolean;
  busy: boolean;                        // a pause or resume request is in flight
  fetching: boolean;                    // a Fetch now request is in flight
  fetchInProgress: boolean;             // a fetch is running in the background
  resumePromptOpen: boolean;            // the resume prompt is showing, not a request
  pauseError?: string;
  onToggleExpanded: () => void;
  onTogglePause: () => void;
  onFetch: () => void;
  onResume: (mode: ResumeMode) => void;
  onCancelResume: () => void;
  onDelete: () => void;
  onSaved: () => void;
}) {
  const { t } = useTranslation();
  return (
    <div style={{ borderBottom: "1px solid #f0f0f0", padding: "0.75rem 0" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", flexWrap: "wrap" }}>
          <span style={{ fontWeight: 600, fontSize: "0.9rem" }}>{source.label || source.identifier}</span>
          {source.label && <span style={{ fontSize: "0.75rem", color: "#aaa", fontFamily: "monospace" }}>{source.identifier}</span>}
          <span style={{ ...pill, ...(SOURCE_BADGE[source.source_type] || {}) }}>{source.source_type}</span>
          {/* The cadence and the filters below are translated here as they are
              in the config panel; the row used to print the raw values (#18). */}
          <span style={{ fontSize: "0.75rem", color: "#999" }}>
            {t(`sources.cadenceOptions.${source.fetch_cadence}`, source.fetch_cadence)}
          </span>
          {source.is_paused && <PausedBadge />}
        </div>
        <div style={{ display: "flex", gap: "0.4rem" }}>
          <button style={btn(false)} onClick={onToggleExpanded}>
            {expanded ? t("common.collapse") : t("sources.configure")}
          </button>
          <PauseButton source={source} busy={busy} onToggle={onTogglePause} />
          <FetchButton source={source} busy={fetching} blocked={workspacePaused} onFetch={onFetch} />
          <button style={btn(false, true)} onClick={onDelete}>{t("common.delete")}</button>
        </div>
      </div>

      <div style={{ display: "flex", gap: "0.3rem", marginTop: "0.3rem", flexWrap: "wrap" }}>
        {(["text", "image", "video", "url"] as const).filter((k) => source.content_filters?.[k]).map((k) => (
          <span key={k} style={{ ...pill, padding: "1px 6px", background: "#f0f4ff", color: "#1a1a2e", border: "1px solid #d0d8ff" }}>
            {t(`sources.${k}`)}
          </span>
        ))}
        {source.backfill_start_date && (
          <span style={{ ...pill, padding: "1px 6px", background: "#fff8e1", color: PAUSED_COLOR, border: "1px solid #ffe082" }}>
            {t("sources.from")} {source.backfill_start_date.slice(0, 10)}
          </span>
        )}
      </div>
      <p style={{ margin: "0.2rem 0 0", fontSize: "0.77rem", color: "#aaa" }}>
        {t("sources.lastFetched")}: {source.last_fetched_at ? new Date(source.last_fetched_at).toLocaleString() : t("sources.never")}
      </p>
      <PausedNotes source={source} error={pauseError} workspacePaused={workspacePaused} />
      {fetchInProgress && (
        <p style={{ margin: "0.15rem 0 0", fontSize: "0.77rem", color: "#1565c0", display: "flex", alignItems: "center", gap: "0.3rem" }}>
          <span style={{ display: "inline-block", width: "8px", height: "8px", borderRadius: "50%", background: "#1565c0", animation: "pulse 1.2s infinite" }} />
          {t("sources.fetchInProgress")}
        </p>
      )}
      {resumePromptOpen && (
        <ResumeDialog
          source={source} busy={busy}
          onChoose={onResume} onCancel={onCancelResume}
        />
      )}

      {expanded && workspaceId && (
        <SourcePanel
          source={source} workspaceId={workspaceId}
          workspacePaused={workspacePaused} onSaved={onSaved}
        />
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

  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [workspaceBusy, setWorkspaceBusy] = useState(false);
  const [workspaceError, setWorkspaceError] = useState("");
  const [connections, setConnections] = useState<Connection[]>([]);
  const [sources, setSources] = useState<Source[]>([]);
  const [fetchingId, setFetchingId] = useState<string | null>(null);
  const [pausingId, setPausingId] = useState<string | null>(null);
  const [pauseErrors, setPauseErrors] = useState<Record<string, string>>({});
  const [resumingId, setResumingId] = useState<string | null>(null);
  const [activeFetches, setActiveFetches] = useState<Record<string, string>>({}); // id → triggered-at ISO
  const [expandedId, setExpandedId] = useState<string | null>(null);

  // Add source form
  const [showAddSource, setShowAddSource] = useState(false);
  const [showAddYoutube, setShowAddYoutube] = useState(false);
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
    const [c, s, w] = await Promise.all([
      api.get<Connection[]>(`/workspaces/${workspaceId}/connections`),
      api.get<Source[]>(`/workspaces/${workspaceId}/sources`),
      api.get<Workspace>(`/workspaces/${workspaceId}`),
    ]);
    setConnections(c.data);
    setSources(s.data);
    setWorkspace(w.data);
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
      setNewIdentifier(""); setNewBackfillDate(""); setShowAddSource(false); setShowAddYoutube(false);
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
    setPauseErrors((prev) => ({ ...prev, [sourceId]: "" }));
    try {
      await api.post(`/workspaces/${workspaceId}/sources/${sourceId}/fetch`);
      setActiveFetches((prev) => ({ ...prev, [sourceId]: new Date().toISOString() }));
    } catch {
      // The backend refuses a paused source or a paused workspace; the buttons
      // are disabled for both, so this is the race, not the normal path.
      setPauseErrors((prev) => ({ ...prev, [sourceId]: t("sources.fetchFailed") }));
    } finally {
      setFetchingId(null);
    }
  };

  const toggleWorkspaceIngestion = async (paused: boolean) => {
    if (!workspaceId) return;
    // Pausing everything is worth confirming; resuming is not, because each
    // source keeps its own paused state and position underneath (#13).
    if (paused && !confirm(t("sources.workspacePauseConfirm"))) return;
    setWorkspaceBusy(true);
    setWorkspaceError("");
    try {
      await api.patch(`/workspaces/${workspaceId}/ingestion`, { paused });
      await load();
    } catch {
      setWorkspaceError(t("sources.workspacePauseFailed"));
    } finally {
      setWorkspaceBusy(false);
    }
  };

  const togglePause = async (source: Source) => {
    if (!workspaceId) return;
    // Resuming asks how — catch up, or skip to now — so it opens the prompt
    // rather than acting (#12). Pausing acts, and says what it keeps.
    if (source.is_paused) {
      setResumingId(source.id);
      return;
    }
    if (!confirm(t("sources.pauseConfirm"))) return;
    setPausingId(source.id);
    setPauseErrors((prev) => ({ ...prev, [source.id]: "" }));
    try {
      await api.patch(`/workspaces/${workspaceId}/sources/${source.id}`, {
        is_paused: true,
      });
      // A pause cancels queued work, so last_fetched_at will never advance and
      // the fetch-in-progress line would otherwise pulse forever.
      setActiveFetches((prev) => {
        const next = { ...prev };
        delete next[source.id];
        return next;
      });
      await load();
    } catch (err: unknown) {
      setPauseErrors((prev) => ({
        ...prev,
        [source.id]:
          (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
          ?? t("common.error"),
      }));
    } finally {
      setPausingId(null);
    }
  };

  const resume = async (source: Source, mode: ResumeMode) => {
    if (!workspaceId) return;
    setPausingId(source.id);
    setPauseErrors((prev) => ({ ...prev, [source.id]: "" }));
    try {
      await api.post(`/workspaces/${workspaceId}/sources/${source.id}/resume`, { mode });
      setResumingId(null);
      await load();
    } catch {
      setPauseErrors((prev) => ({ ...prev, [source.id]: t("sources.resumeFailed") }));
    } finally {
      setPausingId(null);
    }
  };

  const removeSource = async (sourceId: string) => {
    if (!workspaceId || !confirm(t("sources.removeSource") + "?")) return;
    await api.delete(`/workspaces/${workspaceId}/sources/${sourceId}`);
    await load();
  };

  const activeTelegramConns = connections.filter((c) => c.status === "active");
  const youtubeCount = sources.filter((s) => s.source_type === "youtube").length;

  return (
    <div style={{ minHeight: "100vh", background: "#f5f5f5", fontFamily: "system-ui, sans-serif" }}>
      <style>{PULSE_STYLE}</style>
      <NavBar />
      <main style={{ maxWidth: "820px", margin: "0 auto", padding: "2rem 1.5rem" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginBottom: "1.5rem" }}>
          <h1 style={{ fontSize: "1.4rem", color: "#1a1a2e", margin: 0 }}>{t("sources.title")}</h1>
          <HelpLink section="sources" />
        </div>

        {workspace && (
          <WorkspaceIngestion
            workspace={workspace} busy={workspaceBusy} error={workspaceError || undefined}
            onToggle={toggleWorkspaceIngestion}
          />
        )}

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

        {/* YouTube Channels */}
        <div style={sec}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.75rem" }}>
            <h2 style={{ margin: 0, fontSize: "1rem" }}>{t("sources.youtube")}</h2>
            <button style={btn()} onClick={() => { handleSourceTypeChange("youtube"); setShowAddYoutube(true); }}>
              {t("sources.addSource")}
            </button>
          </div>

          {showAddYoutube && (
            <form onSubmit={addSource} style={{ background: "#f9f9f9", padding: "1rem", borderRadius: "6px", marginBottom: "1rem", display: "flex", flexDirection: "column", gap: "0.55rem" }}>
              <label style={lbl}>{t("sources.youtubeIdentifier")}</label>
              <input style={inp} value={newIdentifier} onChange={(e) => setNewIdentifier(e.target.value)}
                placeholder="@ChannelName or UCxxxxxxx or PLxxxxxxx" required />
              <p style={{ margin: 0, fontSize: "0.78rem", color: "#888" }}>{t("sources.youtubeNote")}</p>
              <p style={{ margin: 0, fontSize: "0.82rem", color: "#555" }}>{t("sources.noConnectionNeeded")}</p>

              <label style={lbl}>{t("sources.cadence")}</label>
              <select style={inp} value={newCadence} onChange={(e) => setNewCadence(e.target.value)}>
                <option value="realtime">{t("sources.cadenceOptions.realtime")}</option>
                <option value="hourly">{t("sources.cadenceOptions.hourly")}</option>
                <option value="daily">{t("sources.cadenceOptions.daily")}</option>
              </select>

              <label style={{ ...lbl, marginTop: "0.25rem" }}>{t("sources.backfillStartDate")}</label>
              <input type="date" style={{ ...inp, width: "auto" }} value={newBackfillDate} onChange={(e) => setNewBackfillDate(e.target.value)} />
              <p style={{ margin: 0, fontSize: "0.76rem", color: "#888" }}>{t("sources.backfillStartDateHint")}</p>

              {addError && <p style={{ margin: 0, color: "#c0392b", fontSize: "0.82rem" }}>{addError}</p>}
              <div style={{ display: "flex", gap: "0.5rem", marginTop: "0.25rem" }}>
                <button type="submit" style={btn()} disabled={addingSource}>{addingSource ? t("common.loading") : t("sources.addSource")}</button>
                <button type="button" style={btn(false)} onClick={() => { setShowAddYoutube(false); setAddError(""); }}>{t("common.cancel")}</button>
              </div>
            </form>
          )}

          {/* Deliberately no list here: a channel's controls live once, in the
              sources list below (#18). This says where to find them. */}
          {/* The empty-state hint stays out of the way while the form is open,
              as it did before the lists were consolidated. */}
          {(youtubeCount > 0 || !showAddYoutube) && (
            <p style={{ color: "#888", fontSize: "0.82rem", margin: 0 }}>
              {youtubeCount === 0
                ? t("sources.youtubeNone")
                : t("sources.youtubeTrackedBelow", { count: youtubeCount })}
            </p>
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
              <SourceRow
                key={s.id}
                source={s}
                workspaceId={workspaceId}
                workspacePaused={!!workspace?.ingestion_paused}
                expanded={expandedId === s.id}
                busy={pausingId === s.id}
                fetching={fetchingId === s.id}
                fetchInProgress={!!activeFetches[s.id]}
                resumePromptOpen={resumingId === s.id}
                pauseError={pauseErrors[s.id]}
                onToggleExpanded={() => setExpandedId(expandedId === s.id ? null : s.id)}
                onTogglePause={() => togglePause(s)}
                onFetch={() => fetchNow(s.id)}
                onResume={(mode) => resume(s, mode)}
                onCancelResume={() => setResumingId(null)}
                onDelete={() => removeSource(s.id)}
                onSaved={load}
              />
            ))
          )}
        </div>
      </main>
    </div>
  );
}
