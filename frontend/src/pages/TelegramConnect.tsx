import { useState, useEffect, FormEvent } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import api from "../api/client";
import { NavBar } from "../components/NavBar";

type Step = "credentials" | "otp" | "select" | "done";
type Dialog = { id: string; name: string; type: string; identifier: string };

const TYPE_BADGE: Record<string, React.CSSProperties> = {
  channel:    { background: "#e3f2fd", color: "#1565c0" },
  supergroup: { background: "#f3e5f5", color: "#6a1b9a" },
  group:      { background: "#e8f5e9", color: "#2e7d32" },
};

export function TelegramConnect() {
  const { t } = useTranslation();
  const { workspaceId } = useParams<{ workspaceId: string }>();
  const navigate = useNavigate();

  const [step, setStep] = useState<Step>("credentials");
  const [apiId, setApiId] = useState("");
  const [apiHash, setApiHash] = useState("");
  const [phone, setPhone] = useState("");
  const [label, setLabel] = useState("Main Telegram");
  const [connectionId, setConnectionId] = useState("");
  const [code, setCode] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const [dialogs, setDialogs] = useState<Dialog[]>([]);
  const [loadingDialogs, setLoadingDialogs] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [adding, setAdding] = useState(false);
  const [backfillDate, setBackfillDate] = useState("");

  const card: React.CSSProperties = {
    background: "#fff", border: "1px solid #e0e0e0", borderRadius: "8px",
    padding: "2rem", maxWidth: "520px", margin: "2rem auto",
    fontFamily: "system-ui, sans-serif",
  };
  const inp: React.CSSProperties = {
    width: "100%", padding: "0.6rem 0.75rem", border: "1px solid #ddd",
    borderRadius: "4px", fontSize: "0.95rem", boxSizing: "border-box",
    marginTop: "0.25rem", marginBottom: "0.75rem",
  };
  const btn = (primary = true): React.CSSProperties => ({
    width: "100%", padding: "0.7rem",
    background: primary ? "#1a1a2e" : "transparent",
    color: primary ? "#fff" : "#1a1a2e",
    border: primary ? "none" : "1px solid #1a1a2e",
    borderRadius: "4px", cursor: "pointer", fontWeight: 600,
    fontSize: "0.95rem", marginTop: "0.5rem",
  });
  const lbl: React.CSSProperties = { fontSize: "0.85rem", fontWeight: 500, color: "#555" };

  const sendOtp = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    setSubmitting(true);
    try {
      const { data } = await api.post(`/workspaces/${workspaceId}/connections/telegram/start`, {
        api_id: parseInt(apiId), api_hash: apiHash, phone, label,
      });
      setConnectionId(data.connection_id);
      setStep("otp");
    } catch {
      setError(t("telegramConnect.error"));
    } finally {
      setSubmitting(false);
    }
  };

  const verify = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    setSubmitting(true);
    try {
      await api.post(`/workspaces/${workspaceId}/connections/telegram/${connectionId}/verify`, { code });
      setStep("select");
    } catch {
      setError(t("telegramConnect.error"));
    } finally {
      setSubmitting(false);
    }
  };

  useEffect(() => {
    if (step !== "select" || !connectionId) return;
    setLoadingDialogs(true);
    api.get<Dialog[]>(`/workspaces/${workspaceId}/connections/telegram/${connectionId}/dialogs`)
      .then(({ data }) => setDialogs(data))
      .catch(() => setError(t("telegramConnect.error")))
      .finally(() => setLoadingDialogs(false));
  }, [step, connectionId, workspaceId]);

  const toggleDialog = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  const addSelected = async () => {
    if (selected.size === 0) { setStep("done"); return; }
    setAdding(true);
    setError("");
    const chosen = dialogs.filter((d) => selected.has(d.id));
    try {
      await Promise.all(chosen.map((d) =>
        api.post(`/workspaces/${workspaceId}/sources`, {
          source_type: "telegram",
          identifier: d.identifier,
          platform_connection_id: connectionId,
          fetch_cadence: "hourly",
          content_filters: { text: true, image: false, video: false, url: false },
          backfill_start_date: backfillDate ? new Date(backfillDate).toISOString() : null,
        })
      ));
      setStep("done");
    } catch {
      setError(t("telegramConnect.error"));
    } finally {
      setAdding(false);
    }
  };

  return (
    <div style={{ minHeight: "100vh", background: "#f5f5f5" }}>
      <NavBar />
      <div style={card}>
        <h2 style={{ fontSize: "1.2rem", color: "#1a1a2e", marginTop: 0 }}>{t("telegramConnect.title")}</h2>

        {step === "credentials" && (
          <>
            <p style={{ fontSize: "0.85rem", color: "#666" }}>{t("telegramConnect.intro")}</p>
            {error && <p style={{ color: "#c0392b", fontSize: "0.85rem" }}>{error}</p>}
            <form onSubmit={sendOtp}>
              <label style={lbl}>{t("telegramConnect.apiId")}</label>
              <input style={inp} value={apiId} onChange={(e) => setApiId(e.target.value)} required />
              <label style={lbl}>{t("telegramConnect.apiHash")}</label>
              <input style={inp} value={apiHash} onChange={(e) => setApiHash(e.target.value)} required />
              <label style={lbl}>{t("telegramConnect.phone")}</label>
              <input style={inp} value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="+1234567890" required />
              <label style={lbl}>{t("telegramConnect.label")}</label>
              <input style={inp} value={label} onChange={(e) => setLabel(e.target.value)} required />
              <button type="submit" style={btn()} disabled={submitting}>
                {submitting ? t("telegramConnect.sending") : t("telegramConnect.sendCode")}
              </button>
            </form>
          </>
        )}

        {step === "otp" && (
          <>
            <p style={{ fontSize: "0.875rem", color: "#555" }}>{t("telegramConnect.enterCode")}</p>
            {error && <p style={{ color: "#c0392b", fontSize: "0.85rem" }}>{error}</p>}
            <form onSubmit={verify}>
              <label style={lbl}>{t("telegramConnect.code")}</label>
              <input style={inp} value={code} onChange={(e) => setCode(e.target.value)} required autoFocus />
              <button type="submit" style={btn()} disabled={submitting}>
                {submitting ? t("telegramConnect.verifying") : t("telegramConnect.verify")}
              </button>
            </form>
          </>
        )}

        {step === "select" && (
          <>
            <p style={{ fontSize: "0.875rem", color: "#1a1a2e", fontWeight: 600, marginBottom: "0.25rem" }}>
              {t("telegramConnect.selectChannels")}
            </p>
            <p style={{ fontSize: "0.82rem", color: "#666", marginTop: 0, marginBottom: "0.75rem" }}>
              {t("telegramConnect.selectIntro")}
            </p>
            {error && <p style={{ color: "#c0392b", fontSize: "0.85rem" }}>{error}</p>}

            {loadingDialogs ? (
              <p style={{ color: "#888", fontSize: "0.875rem" }}>{t("telegramConnect.loadingChannels")}</p>
            ) : dialogs.length === 0 ? (
              <p style={{ color: "#888", fontSize: "0.875rem" }}>{t("telegramConnect.noChannels")}</p>
            ) : (
              <div style={{ maxHeight: "320px", overflowY: "auto", border: "1px solid #eee", borderRadius: "6px", marginBottom: "0.75rem" }}>
                {dialogs.map((d) => (
                  <label
                    key={d.id}
                    style={{
                      display: "flex", alignItems: "center", gap: "0.6rem",
                      padding: "0.6rem 0.75rem", cursor: "pointer",
                      borderBottom: "1px solid #f5f5f5",
                      background: selected.has(d.id) ? "#f0f4ff" : "transparent",
                    }}
                  >
                    <input
                      type="checkbox"
                      checked={selected.has(d.id)}
                      onChange={() => toggleDialog(d.id)}
                      style={{ accentColor: "#1a1a2e" }}
                    />
                    <span style={{ fontSize: "0.875rem", fontWeight: 500, flex: 1 }}>{d.name}</span>
                    <span style={{ fontSize: "0.7rem", padding: "2px 7px", borderRadius: "10px", ...(TYPE_BADGE[d.type] || {}) }}>
                      {d.type}
                    </span>
                  </label>
                ))}
              </div>
            )}

            <div style={{ marginTop: "0.75rem", marginBottom: "0.25rem" }}>
              <label style={{ fontSize: "0.82rem", fontWeight: 500, color: "#555", display: "block", marginBottom: "0.2rem" }}>
                {t("sources.backfillStartDate")}
              </label>
              <input type="date" value={backfillDate} onChange={(e) => setBackfillDate(e.target.value)}
                style={{ padding: "0.4rem 0.6rem", border: "1px solid #ddd", borderRadius: "4px", fontSize: "0.85rem" }} />
              <p style={{ margin: "0.2rem 0 0", fontSize: "0.76rem", color: "#888" }}>{t("sources.backfillStartDateHint")}</p>
            </div>

            <p style={{ fontSize: "0.78rem", color: "#888", margin: "0.5rem 0 0.5rem" }}>
              {selected.size} {t("telegramConnect.selectedCount")}
            </p>

            <button style={btn()} onClick={addSelected} disabled={adding || loadingDialogs}>
              {adding ? t("telegramConnect.adding") : t("telegramConnect.addSelected")}
            </button>
            <button style={btn(false)} onClick={() => setStep("done")} disabled={adding}>
              {t("telegramConnect.skipForNow")}
            </button>
          </>
        )}

        {step === "done" && (
          <>
            <p style={{ color: "#27ae60", fontWeight: 600 }}>{t("telegramConnect.connected")}</p>
            <button style={btn()} onClick={() => navigate("/sources")}>
              {t("common.back")}
            </button>
          </>
        )}
      </div>
    </div>
  );
}
