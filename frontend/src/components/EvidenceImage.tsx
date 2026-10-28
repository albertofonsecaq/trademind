import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import api from "../api/client";

/**
 * The picture a piece of image evidence was read from (#108), as a thumbnail
 * that opens full size.
 *
 * Fetched through the API client rather than an <img src>, because the image
 * is served only to members and the token travels in a header, not a cookie.
 */
export function EvidenceImage({ workspaceId, evidenceId }: { workspaceId: string; evidenceId: string }) {
  const { t } = useTranslation();
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let objectUrl: string | null = null;
    let cancelled = false;
    api
      .get<Blob>(`/workspaces/${workspaceId}/evidence/${evidenceId}/image`, { responseType: "blob" })
      .then(({ data }) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(data);
        setUrl(objectUrl);
      })
      .catch(() => { if (!cancelled) setFailed(true); });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [workspaceId, evidenceId]);

  useEffect(() => {
    if (!open) return;
    const close = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [open]);

  if (failed) return null;
  if (!url) {
    return <div style={{ width: "160px", height: "100px", background: "#f0f0f0", borderRadius: "4px", marginBottom: "0.4rem" }} />;
  }

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        title={t("evidenceImage.enlarge")}
        style={{ display: "block", padding: 0, border: "1px solid #e0e0e0", borderRadius: "4px", background: "none", cursor: "zoom-in", marginBottom: "0.4rem" }}
      >
        <img src={url} alt={t("evidenceImage.alt")} style={{ display: "block", maxWidth: "240px", maxHeight: "140px", borderRadius: "4px" }} />
      </button>
      {open && (
        <div
          role="dialog"
          aria-label={t("evidenceImage.alt")}
          onClick={() => setOpen(false)}
          style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.8)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000, cursor: "zoom-out", padding: "16px" }}
        >
          <img src={url} alt={t("evidenceImage.alt")} style={{ maxWidth: "100%", maxHeight: "100%", borderRadius: "4px" }} />
        </div>
      )}
    </>
  );
}
