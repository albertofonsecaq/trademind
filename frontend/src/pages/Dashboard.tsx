import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { NavBar } from "../components/NavBar";
import { useAuth } from "../context/AuthContext";
import { OnboardingModal, useOnboarding } from "../components/OnboardingModal";
import api, { type Workspace } from "../api/client";

export function Dashboard() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const { isDone, markDone } = useOnboarding();
  const [showOnboarding, setShowOnboarding] = useState(false);

  useEffect(() => {
    api.get<Workspace>("/workspaces/current").then((r) => setWorkspace(r.data)).catch(() => {});
    // Show onboarding walkthrough only on first login
    if (!isDone()) setShowOnboarding(true);
  }, []);

  const cardBtn: React.CSSProperties = {
    display: "inline-block",
    marginTop: "1rem",
    padding: "0.65rem 1.25rem",
    background: "#1a1a2e",
    color: "#fff",
    borderRadius: "6px",
    textDecoration: "none",
    fontWeight: 600,
    fontSize: "0.9rem",
  };

  return (
    <div style={{ minHeight: "100vh", background: "#f5f5f5", fontFamily: "system-ui, sans-serif" }}>
      <NavBar />
      <main style={{ padding: "2.5rem 2rem", maxWidth: "800px", margin: "0 auto" }}>
        <h1 style={{ fontSize: "1.8rem", color: "#1a1a2e", marginBottom: "0.4rem" }}>
          {t("dashboard.welcome")}
        </h1>
        {workspace && (
          <p style={{ color: "#666", marginBottom: "2rem" }}>
            {t("dashboard.workspace")}: <strong>{workspace.name}</strong>
          </p>
        )}

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "1rem" }}>
          <div style={{ background: "#fff", border: "1px solid #e0e0e0", borderRadius: "8px", padding: "1.5rem" }}>
            <h2 style={{ fontSize: "1rem", margin: "0 0 0.5rem" }}>{t("ask.title")}</h2>
            <p style={{ fontSize: "0.875rem", color: "#666", margin: 0 }}>{t("dashboard.description")}</p>
            <Link to="/ask" style={cardBtn}>{t("dashboard.goToAsk")}</Link>
          </div>
          <div style={{ background: "#fff", border: "1px solid #e0e0e0", borderRadius: "8px", padding: "1.5rem" }}>
            <h2 style={{ fontSize: "1rem", margin: "0 0 0.5rem" }}>{t("sources.title")}</h2>
            <p style={{ fontSize: "0.875rem", color: "#666", margin: 0 }}>{t("sources.noSources")}</p>
            <Link to="/sources" style={cardBtn}>{t("dashboard.manageSources")}</Link>
          </div>
          <div style={{ background: "#fff", border: "1px solid #e0e0e0", borderRadius: "8px", padding: "1.5rem" }}>
            <h2 style={{ fontSize: "1rem", margin: "0 0 0.5rem" }}>{t("help.title")}</h2>
            <p style={{ fontSize: "0.875rem", color: "#666", margin: 0 }}>{t("dashboard.helpDescription")}</p>
            <Link to="/help" style={cardBtn}>{t("dashboard.goToHelp")}</Link>
          </div>
        </div>
      </main>

      {showOnboarding && (
        <OnboardingModal onDone={() => { markDone(); setShowOnboarding(false); }} />
      )}
    </div>
  );
}
