import { useState, FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useAuth } from "../context/AuthContext";
import { LanguageToggle } from "../components/LanguageToggle";
import { styles } from "./authStyles";

export function Register() {
  const { t, i18n } = useTranslation();
  const { register } = useAuth();
  const navigate = useNavigate();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    if (password.length < 8) { setError(t("auth.passwordMinLength")); return; }
    setSubmitting(true);
    try {
      await register(email, password, i18n.language);
      navigate("/", { replace: true });
    } catch (err: unknown) {
      const status = (err as { response?: { status?: number } })?.response?.status;
      setError(status === 409 ? t("auth.emailTaken") : t("auth.registerError"));
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
        <h2 style={styles.title}>{t("auth.registerTitle")}</h2>
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
            minLength={8}
            style={styles.input}
            autoComplete="new-password"
          />
          {error && <p style={styles.error}>{error}</p>}
          <button type="submit" disabled={submitting} style={styles.button}>
            {submitting ? t("common.loading") : t("auth.register")}
          </button>
        </form>
        <p style={styles.switchText}>
          {t("auth.haveAccount")}{" "}
          <Link to="/login" style={styles.link}>{t("auth.loginLink")}</Link>
        </p>
      </div>
    </div>
  );
}
