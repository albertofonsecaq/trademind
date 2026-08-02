import { createContext, useContext, useState, useEffect, ReactNode } from "react";
import { useTranslation } from "react-i18next";
import api, { type User } from "../api/client";

type AuthState = {
  user: User | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, lang: string) => Promise<void>;
  logout: () => void;
  setLanguage: (lang: string) => Promise<void>;
};

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const { i18n } = useTranslation();

  useEffect(() => {
    const token = localStorage.getItem("access_token");
    if (!token) { setLoading(false); return; }
    api.get<User>("/auth/me")
      .then((res) => {
        setUser(res.data);
        i18n.changeLanguage(res.data.preferred_language);
      })
      .catch(() => {
        localStorage.removeItem("access_token");
        localStorage.removeItem("refresh_token");
      })
      .finally(() => setLoading(false));
  }, [i18n]);

  const login = async (email: string, password: string) => {
    const form = new URLSearchParams({ username: email, password });
    const { data } = await api.post("/auth/token", form, {
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
    });
    localStorage.setItem("access_token", data.access_token);
    localStorage.setItem("refresh_token", data.refresh_token);
    const me = await api.get<User>("/auth/me");
    setUser(me.data);
    i18n.changeLanguage(me.data.preferred_language);
  };

  const register = async (email: string, password: string, lang: string) => {
    const { data } = await api.post("/auth/register", { email, password, preferred_language: lang });
    localStorage.setItem("access_token", data.access_token);
    localStorage.setItem("refresh_token", data.refresh_token);
    const me = await api.get<User>("/auth/me");
    setUser(me.data);
    i18n.changeLanguage(lang);
  };

  const logout = () => {
    localStorage.removeItem("access_token");
    localStorage.removeItem("refresh_token");
    setUser(null);
  };

  const setLanguage = async (lang: string) => {
    await api.patch("/users/me", { preferred_language: lang });
    setUser((u) => u ? { ...u, preferred_language: lang } : u);
    i18n.changeLanguage(lang);
    localStorage.setItem("trademind_lang", lang);
  };

  return (
    <AuthContext.Provider value={{ user, loading, login, register, logout, setLanguage }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
