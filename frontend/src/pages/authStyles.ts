import type { CSSProperties } from "react";

export const styles: Record<string, CSSProperties> = {
  page: {
    minHeight: "100vh",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    background: "#f5f5f5",
    fontFamily: "system-ui, sans-serif",
  },
  card: {
    background: "#fff",
    borderRadius: "8px",
    boxShadow: "0 2px 12px rgba(0,0,0,0.1)",
    padding: "2rem",
    width: "100%",
    maxWidth: "400px",
  },
  topBar: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    marginBottom: "0.5rem",
  },
  logo: {
    margin: 0,
    fontSize: "1.5rem",
    fontWeight: 700,
    color: "#1a1a2e",
  },
  title: {
    fontSize: "1.1rem",
    fontWeight: 500,
    color: "#333",
    marginBottom: "1.5rem",
    marginTop: "0.25rem",
  },
  form: {
    display: "flex",
    flexDirection: "column",
    gap: "0.4rem",
  },
  label: {
    fontSize: "0.875rem",
    fontWeight: 500,
    color: "#555",
    marginTop: "0.6rem",
  },
  input: {
    padding: "0.6rem 0.75rem",
    border: "1px solid #ddd",
    borderRadius: "4px",
    fontSize: "1rem",
    outline: "none",
  },
  button: {
    marginTop: "1rem",
    padding: "0.75rem",
    background: "#1a1a2e",
    color: "#fff",
    border: "none",
    borderRadius: "4px",
    fontSize: "1rem",
    cursor: "pointer",
    fontWeight: 600,
  },
  error: {
    color: "#c0392b",
    fontSize: "0.85rem",
    margin: "0.25rem 0 0",
  },
  switchText: {
    marginTop: "1.25rem",
    textAlign: "center",
    fontSize: "0.875rem",
    color: "#666",
  },
  link: {
    color: "#1a1a2e",
    fontWeight: 600,
    textDecoration: "none",
  },
};
