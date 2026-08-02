import { useState, FormEvent } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import api from "../api/client";
import { NavBar } from "../components/NavBar";

export function TelegramConnect() {
  const { t } = useTranslation();
  const { workspaceId } = useParams<{ workspaceId: string }>();
  const navigate = useNavigate();

  const [step, setStep] = useState<"credentials" | "otp" | "done">("credentials");
  const [apiId, setApiId] = useState("");
  const [apiHash, setApiHash] = useState("");
  const [phone, setPhone] = useState("");
  const [label, setLabel] = useState("Main Telegram");
  const [connectionId, setConnectionId] = useState("");
  const [code, setCode] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const sendOtp = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    setSubmitting(true);
    try {
      const { data } = await api.post(`/workspaces/${workspaceId}/connections/telegram/start`, {
        api_id: parseInt(apiId),
        api_hash: apiHash,
        phone,
        label,
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
      setStep("done");
    } catch {
      setError(t("telegramConnect.error"));
    } finally {
      setSubmitting(false);
    }
  };

  const card: React.CSSProperties = {
    background: "#fff",
    border: "1px solid #e0e0e0",
    borderRadius: "8px",
    padding: "2rem",
    maxWidth: "480px",
    margin: "2rem auto",
    fontFamily: "system-ui, sans-serif",
  };

  const inp: React.CSSProperties = {
    width: "100%",
    padding: "0.6rem 0.75rem",
    border: "1px solid #ddd",
    borderRadius: "4px",
    fontSize: "0.95rem",
    boxSizing: "border-box",
    marginTop: "0.25rem",
    marginBottom: "0.75rem",
  };

  const btn: React.CSSProperties = {
    width: "100%",
    padding: "0.7rem",
    background: "#1a1a2e",
    color: "#fff",
    border: "none",
    borderRadius: "4px",
    cursor: "pointer",
    fontWeight: 600,
    fontSize: "0.95rem",
    marginTop: "0.5rem",
  };

  const lbl: React.CSSProperties = { fontSize: "0.85rem", fontWeight: 500, color: "#555" };

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
              <button type="submit" style={btn} disabled={submitting}>
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
              <button type="submit" style={btn} disabled={submitting}>
                {submitting ? t("telegramConnect.verifying") : t("telegramConnect.verify")}
              </button>
            </form>
          </>
        )}

        {step === "done" && (
          <>
            <p style={{ color: "#27ae60", fontWeight: 600 }}>{t("telegramConnect.connected")}</p>
            <button style={btn} onClick={() => navigate("/sources")}>
              {t("common.back")}
            </button>
          </>
        )}
      </div>
    </div>
  );
}
