import { useState, useEffect, useRef } from "react";
import { useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { NavBar } from "../components/NavBar";

// ── Section registry ──────────────────────────────────────────────────────────
// Order determines display order and sidebar order.

const SECTION_IDS = [
  "ask", "sources", "library", "plan", "trading",
  "billing", "changelog", "admin",
] as const;

type SectionId = typeof SECTION_IDS[number];

// ── Body renderer: \n becomes paragraph breaks ────────────────────────────────

function BodyText({ text }: { text: string }) {
  const paras = text.split("\n\n");
  return (
    <div>
      {paras.map((para, i) => {
        // Render bullet lists (lines starting with •)
        const lines = para.split("\n");
        const hasBullets = lines.some(l => l.startsWith("•"));
        if (hasBullets) {
          return (
            <div key={i} style={{ marginBottom: "0.75rem" }}>
              {lines.map((line, j) =>
                line.startsWith("•") ? (
                  <div key={j} style={{ display: "flex", gap: "0.5rem", fontSize: "0.875rem", color: "#444", lineHeight: 1.6, marginBottom: "0.15rem" }}>
                    <span style={{ color: "#1a1a2e", fontWeight: 600, flexShrink: 0 }}>•</span>
                    <span>{line.slice(1).trim()}</span>
                  </div>
                ) : (
                  <p key={j} style={{ margin: "0 0 0.3rem", fontSize: "0.875rem", color: "#444", lineHeight: 1.6 }}>{line}</p>
                )
              )}
            </div>
          );
        }
        return (
          <p key={i} style={{ margin: "0 0 0.75rem", fontSize: "0.875rem", color: "#444", lineHeight: 1.6 }}>
            {para}
          </p>
        );
      })}
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export function Help() {
  const { t, i18n } = useTranslation();
  const [searchParams, setSearchParams] = useSearchParams();
  const [query, setQuery] = useState("");
  const [activeSection, setActiveSection] = useState<SectionId | null>(
    (searchParams.get("section") as SectionId) || null
  );
  const sectionRefs = useRef<Record<string, HTMLDivElement | null>>({});

  // Scroll to section when navigated via deep link
  useEffect(() => {
    const section = searchParams.get("section") as SectionId | null;
    if (section && sectionRefs.current[section]) {
      sectionRefs.current[section]!.scrollIntoView({ behavior: "smooth", block: "start" });
      setActiveSection(section);
    }
  }, [searchParams, i18n.language]);

  // Build section data from i18n
  const sections = SECTION_IDS.map((id) => ({
    id,
    title: t(`help.sections.${id}.title`),
    summary: t(`help.sections.${id}.summary`),
    body: t(`help.sections.${id}.body`),
  }));

  // Filter sections by search query
  const q = query.trim().toLowerCase();
  const filtered = q
    ? sections.filter(
        (s) =>
          s.title.toLowerCase().includes(q) ||
          s.summary.toLowerCase().includes(q) ||
          s.body.toLowerCase().includes(q)
      )
    : sections;

  const nav: React.CSSProperties = {
    width: "200px",
    flexShrink: 0,
    position: "sticky",
    top: "72px",
    alignSelf: "flex-start",
  };
  const navItem = (id: SectionId): React.CSSProperties => ({
    display: "block", padding: "0.45rem 0.75rem", borderRadius: "6px",
    fontSize: "0.82rem", cursor: "pointer", textDecoration: "none",
    background: activeSection === id ? "#1a1a2e" : "transparent",
    color: activeSection === id ? "#fff" : "#555",
    fontWeight: activeSection === id ? 600 : 400,
    border: "none", width: "100%", textAlign: "left",
    transition: "background 0.15s",
  });

  const scrollTo = (id: SectionId) => {
    setActiveSection(id);
    setSearchParams({ section: id });
    sectionRefs.current[id]?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  return (
    <div style={{ minHeight: "100vh", background: "#f5f5f5", fontFamily: "system-ui, sans-serif" }}>
      <NavBar />
      <main style={{ maxWidth: "1060px", margin: "0 auto", padding: "2rem 1.5rem" }}>
        <h1 style={{ margin: "0 0 0.5rem", fontSize: "1.4rem", color: "#1a1a2e" }}>{t("help.title")}</h1>

        {/* Search */}
        <div style={{ marginBottom: "1.75rem" }}>
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t("help.searchPlaceholder")}
            style={{
              padding: "0.55rem 0.9rem", border: "1px solid #ddd", borderRadius: "6px",
              fontSize: "0.9rem", width: "min(360px, 100%)", outline: "none",
              background: "#fff",
            }}
          />
        </div>

        <div style={{ display: "flex", gap: "2rem", alignItems: "flex-start" }}>
          {/* Sidebar nav — hidden when searching */}
          {!q && (
            <div style={nav}>
              {sections.map((s) => (
                <button key={s.id} style={navItem(s.id as SectionId)} onClick={() => scrollTo(s.id as SectionId)}>
                  {s.title.split("—")[0].trim()}
                </button>
              ))}
            </div>
          )}

          {/* Content */}
          <div style={{ flex: 1, minWidth: 0 }}>
            {filtered.length === 0 ? (
              <div style={{ background: "#fff", border: "1px solid #e0e0e0", borderRadius: "8px", padding: "1.5rem", color: "#888" }}>
                {t("help.noResults")}
              </div>
            ) : (
              filtered.map((section) => (
                <div
                  key={section.id}
                  ref={(el) => { sectionRefs.current[section.id] = el; }}
                  style={{
                    background: "#fff", border: "1px solid #e0e0e0", borderRadius: "8px",
                    padding: "1.5rem 1.75rem", marginBottom: "1.25rem",
                    scrollMarginTop: "80px",
                    borderLeft: activeSection === section.id ? "3px solid #1a1a2e" : "1px solid #e0e0e0",
                    transition: "border-left 0.2s",
                  }}
                  onMouseEnter={() => !q && setActiveSection(section.id as SectionId)}
                >
                  <h2 style={{ margin: "0 0 0.4rem", fontSize: "1.05rem", color: "#1a1a2e" }}>{section.title}</h2>
                  <p style={{ margin: "0 0 1rem", fontSize: "0.875rem", color: "#666", fontStyle: "italic" }}>{section.summary}</p>
                  <BodyText text={section.body} />
                </div>
              ))
            )}
          </div>
        </div>
      </main>
    </div>
  );
}
