import { Navigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { useTranslation } from "react-i18next";

export function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  const { t } = useTranslation();

  if (loading) return <div style={{ padding: "2rem" }}>{t("common.loading")}</div>;
  if (!user) return <Navigate to="/login" replace />;
  return <>{children}</>;
}
