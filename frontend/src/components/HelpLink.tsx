import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";

type Props = {
  section: "ask" | "sources" | "library" | "plan" | "trading" | "billing" | "changelog" | "admin";
  style?: React.CSSProperties;
};

/**
 * Contextual help hint — renders a small "?" link that opens the Help view
 * scrolled to the relevant section. Place inline near the feature it documents.
 */
export function HelpLink({ section, style }: Props) {
  const navigate = useNavigate();
  const { t } = useTranslation();

  return (
    <button
      onClick={() => navigate(`/help?section=${section}`)}
      title={t("help.contextualHint")}
      aria-label={t("help.contextualHint")}
      style={{
        background: "none",
        border: "1px solid #ddd",
        borderRadius: "50%",
        width: "20px",
        height: "20px",
        fontSize: "0.72rem",
        fontWeight: 700,
        color: "#888",
        cursor: "pointer",
        lineHeight: "18px",
        textAlign: "center",
        padding: 0,
        flexShrink: 0,
        ...style,
      }}
    >
      ?
    </button>
  );
}
