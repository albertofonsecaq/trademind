import { useTranslation } from "react-i18next";
import { useAuth } from "../context/AuthContext";

type Props = { standalone?: boolean };

export function LanguageToggle({ standalone = false }: Props) {
  const { t, i18n } = useTranslation();
  const { user, setLanguage } = useAuth();

  const toggle = async () => {
    const next = i18n.language === "en" ? "es" : "en";
    if (user) {
      await setLanguage(next);
    } else {
      i18n.changeLanguage(next);
      localStorage.setItem("trademind_lang", next);
    }
  };

  return (
    <button
      onClick={toggle}
      style={{
        background: "none",
        border: "1px solid #ccc",
        borderRadius: "4px",
        padding: standalone ? "6px 14px" : "4px 10px",
        cursor: "pointer",
        fontSize: "0.85rem",
      }}
      title={t("language.label")}
    >
      {i18n.language === "en" ? "ES" : "EN"}
    </button>
  );
}
