import { Link, useLocation, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useAuth } from "../context/AuthContext";
import { LanguageToggle } from "./LanguageToggle";

const NAV_LINKS = [
  { to: "/ask", labelKey: "nav.ask" },
  { to: "/library", labelKey: "nav.library" },
  { to: "/plan", labelKey: "nav.plan" },
  { to: "/trading", labelKey: "nav.trading" },
  { to: "/changelog", labelKey: "nav.changelog" },
  { to: "/billing", labelKey: "nav.billing" },
  { to: "/sources", labelKey: "nav.sources" },
];

export function NavBar() {
  const { t } = useTranslation();
  const { user, logout } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();

  return (
    <header style={{
      background: "#1a1a2e",
      color: "#fff",
      padding: "0 2rem",
      height: "56px",
      display: "flex",
      alignItems: "center",
      justifyContent: "space-between",
      position: "sticky",
      top: 0,
      zIndex: 100,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: "2rem" }}>
        <Link to="/" style={{ fontWeight: 700, fontSize: "1.2rem", color: "#fff", textDecoration: "none" }}>
          {t("common.appName")}
        </Link>
        <nav style={{ display: "flex", gap: "1.25rem" }}>
          {NAV_LINKS.map(({ to, labelKey }) => (
            <Link
              key={to}
              to={to}
              style={{
                color: location.pathname.startsWith(to) ? "#fff" : "rgba(255,255,255,0.6)",
                textDecoration: "none",
                fontSize: "0.9rem",
                fontWeight: location.pathname.startsWith(to) ? 600 : 400,
                borderBottom: location.pathname.startsWith(to) ? "2px solid #4ade80" : "2px solid transparent",
                paddingBottom: "2px",
              }}
            >
              {t(labelKey)}
            </Link>
          ))}
        </nav>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: "1rem" }}>
        <LanguageToggle />
        <button
          onClick={() => navigate("/help")}
          title={t("help.navTitle")}
          aria-label={t("help.navTitle")}
          style={{
            background: "none", border: "1px solid rgba(255,255,255,0.35)", color: "#fff",
            borderRadius: "50%", width: "28px", height: "28px", fontSize: "0.85rem",
            fontWeight: 700, cursor: "pointer", lineHeight: "26px", textAlign: "center", padding: 0,
          }}
        >
          ?
        </button>
        <span style={{ fontSize: "0.8rem", opacity: 0.6 }}>{user?.email}</span>
        <button
          onClick={logout}
          style={{
            background: "none",
            border: "1px solid rgba(255,255,255,0.35)",
            color: "#fff",
            padding: "4px 12px",
            borderRadius: "4px",
            cursor: "pointer",
            fontSize: "0.82rem",
          }}
        >
          {t("nav.logout")}
        </button>
      </div>
    </header>
  );
}
