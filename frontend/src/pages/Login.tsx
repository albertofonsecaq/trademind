import { useState, FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useAuth } from "../context/AuthContext";
import { LanguageToggle } from "../components/LanguageToggle";
import { styles } from "./authStyles";

export function Login() {
  const { t } = useTranslation();
  const { login } = useAuth();
  const navigate = useNavigate();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    setSubmitting(true);
    try {
      await login(email, password);
      navigate("/", { replace: true });
    } catch {
      setError(t("auth.loginError"));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div style={styles.page}>
      <div style={styles.card}>
        <div style={styles.topBar}>
          <h1 style={styles.logo}>{t("common.appName")}</h1>
          <LanguageToggle />
        </div>
        <h2 style={styles.title}>{t("auth.loginTitle")}</h2>
        <form onSubmit={handleSubmit} style={styles.form}>
          <label style={styles.label}>{t("auth.email")}</label>
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            style={styles.input}
            autoComplete="email"
          />
          <label style={styles.label}>{t("auth.password")}</label>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            style={styles.input}
            autoComplete="current-password"
          />
          {error && <p style={styles.error}>{error}</p>}
          <button type="submit" disabled={submitting} style={styles.button}>
            {submitting ? t("common.loading") : t("auth.login")}
          </button>
        </form>
        <p style={styles.switchText}>
          {t("auth.noAccount")}{" "}
          <Link to="/register" style={styles.link}>{t("auth.registerLink")}</Link>
        </p>
      </div>
    </div>
  );
}
