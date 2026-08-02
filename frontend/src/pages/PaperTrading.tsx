import { useState, useEffect, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router-dom";
import { NavBar } from "../components/NavBar";
import api from "../api/client";

// ── types ─────────────────────────────────────────────────────────────────────

type BrokerOrder = {
  id: string; user_id: string; strategy_card_id: string | null;
  symbol: string; action: string; entry: string | null; target: string | null;
  stop: string | null; size: string; status: string; mode: string;
  notes: string | null; confirmed_at: string | null; alpaca_order_id: string | null;
  filled_price: string | null; created_at: string; updated_at: string;
};

type JournalEntry = {
  id: string; symbol: string; action: string; entry: string; exit: string | null;
  size: string; mode: string; notes: string | null; timestamp: string;
  outcome: string | null;
  target_price: string | null; stop_price: string | null;
  max_adverse_excursion: string | null; max_favorable_excursion: string | null;
};

// ── style helpers ─────────────────────────────────────────────────────────────

const STATUS_COLORS: Record<string, string> = {
  proposed: "#888", confirmed: "#1565c0", submitted: "#e67e22",
  filled: "#27ae60", rejected: "#c0392b", cancelled: "#bbb",
};
const ACTION_COLORS: Record<string, string> = {
  long: "#27ae60", short: "#c0392b",
};

const badge = (color: string): React.CSSProperties => ({
  display: "inline-block", fontSize: "0.71rem", padding: "2px 8px",
  borderRadius: "10px", background: color + "22", color, fontWeight: 600,
  border: `1px solid ${color}44`,
});
const card: React.CSSProperties = {
  background: "#fff", border: "1px solid #e0e0e0", borderRadius: "8px",
  padding: "1rem 1.25rem", marginBottom: "0.6rem",
};
const btn = (bg: string, color = "#fff"): React.CSSProperties => ({
  padding: "4px 12px", fontSize: "0.78rem", borderRadius: "4px", cursor: "pointer",
  border: "none", background: bg, color, fontWeight: 600,
});
const outlineBtn: React.CSSProperties = {
  padding: "4px 12px", fontSize: "0.78rem", borderRadius: "4px", cursor: "pointer",
  border: "1px solid #ddd", background: "transparent", color: "#555",
};
const inp: React.CSSProperties = {
  padding: "0.4rem 0.65rem", border: "1px solid #ddd", borderRadius: "4px",
  fontSize: "0.875rem", width: "100%", boxSizing: "border-box",
};

// ── Propose modal ─────────────────────────────────────────────────────────────

type ProposeModalProps = {
  t: (k: string) => string;
  initial?: { symbol?: string; action?: string; entry?: string; target?: string; stop?: string; cardId?: string };
  onClose: () => void;
  onCreated: (order: BrokerOrder) => void;
};

function ProposeModal({ t, initial = {}, onClose, onCreated }: ProposeModalProps) {
  const [symbol, setSymbol] = useState(initial.symbol || "");
  const [action, setAction] = useState(initial.action || "long");
  const [entry, setEntry] = useState(initial.entry || "");
  const [target, setTarget] = useState(initial.target || "");
  const [stop, setStop] = useState(initial.stop || "");
  const [size, setSize] = useState("");
  const [notes, setNotes] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const propose = async () => {
    if (!symbol.trim() || !size.trim()) { setError("Symbol and size are required."); return; }
    setSubmitting(true); setError("");
    try {
      const resp = await api.post<BrokerOrder>("/users/me/orders", {
        symbol: symbol.toUpperCase(),
        action,
        entry: entry ? parseFloat(entry) : null,
        target: target ? parseFloat(target) : null,
        stop: stop ? parseFloat(stop) : null,
        size: parseFloat(size),
        strategy_card_id: initial.cardId || null,
        notes: notes || null,
      });
      onCreated(resp.data);
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setError(msg || t("common.error"));
    } finally {
      setSubmitting(false);
    }
  };

  const overlay: React.CSSProperties = {
    position: "fixed", inset: 0, background: "rgba(0,0,0,0.45)", zIndex: 200,
    display: "flex", alignItems: "center", justifyContent: "center",
  };
  const modal: React.CSSProperties = {
    background: "#fff", borderRadius: "10px", padding: "1.75rem 2rem",
    width: "min(480px, 94vw)", boxShadow: "0 8px 32px rgba(0,0,0,0.18)",
  };
  const label: React.CSSProperties = {
    display: "block", fontSize: "0.8rem", fontWeight: 600, color: "#555", marginBottom: "0.25rem",
  };
  const field: React.CSSProperties = { marginBottom: "0.85rem" };

  return (
    <div style={overlay} onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div style={modal}>
        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "1.25rem" }}>
          <h2 style={{ margin: 0, fontSize: "1.1rem", color: "#1a1a2e" }}>{t("trading.modalTitle")}</h2>
          <button onClick={onClose} style={{ background: "none", border: "none", fontSize: "1.2rem", cursor: "pointer", color: "#888" }}>×</button>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0 1rem" }}>
          <div style={field}>
            <label style={label}>{t("trading.symbol")}</label>
            <input style={inp} value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())} placeholder="AAPL" />
          </div>
          <div style={field}>
            <label style={label}>{t("trading.action")}</label>
            <select style={inp} value={action} onChange={(e) => setAction(e.target.value)}>
              <option value="long">{t("trading.long")}</option>
              <option value="short">{t("trading.short")}</option>
            </select>
          </div>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "0 0.75rem" }}>
          <div style={field}>
            <label style={label}>{t("trading.entry")}</label>
            <input style={inp} type="number" step="0.01" min="0" value={entry} onChange={(e) => setEntry(e.target.value)} placeholder={t("trading.entryPlaceholder")} />
          </div>
          <div style={field}>
            <label style={label}>{t("trading.target")}</label>
            <input style={inp} type="number" step="0.01" min="0" value={target} onChange={(e) => setTarget(e.target.value)} placeholder={t("trading.entryPlaceholder")} />
          </div>
          <div style={field}>
            <label style={label}>{t("trading.stop")}</label>
            <input style={inp} type="number" step="0.01" min="0" value={stop} onChange={(e) => setStop(e.target.value)} placeholder={t("trading.entryPlaceholder")} />
          </div>
        </div>

        <div style={field}>
          <label style={label}>{t("trading.size")}</label>
          <input style={inp} type="number" step="1" min="0" value={size} onChange={(e) => setSize(e.target.value)} placeholder="10" />
        </div>

        <div style={field}>
          <label style={label}>{t("trading.notes")}</label>
          <textarea rows={2} style={{ ...inp, resize: "vertical" }} value={notes} onChange={(e) => setNotes(e.target.value)} placeholder={t("trading.notesPlaceholder")} />
        </div>

        {error && <p style={{ color: "#c0392b", fontSize: "0.82rem", margin: "0 0 0.75rem" }}>{error}</p>}

        <div style={{ display: "flex", gap: "0.6rem", justifyContent: "flex-end" }}>
          <button onClick={onClose} style={outlineBtn}>{t("common.cancel")}</button>
          <button
            onClick={propose}
            disabled={submitting}
            style={{ ...btn("#1a1a2e"), opacity: submitting ? 0.65 : 1 }}
          >
            {submitting ? t("trading.proposing") : t("trading.proposeBtn")}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── OrderCard ─────────────────────────────────────────────────────────────────

type OrderCardProps = {
  order: BrokerOrder;
  t: (k: string) => string;
  onUpdated: (o: BrokerOrder) => void;
};

function OrderCard({ order, t, onUpdated }: OrderCardProps) {
  const [busy, setBusy] = useState<string | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [error, setError] = useState("");

  const act = async (path: string, label: string) => {
    setBusy(label); setError("");
    try {
      const resp = await api.post<BrokerOrder>(`/users/me/orders/${order.id}/${path}`);
      onUpdated(resp.data);
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setError(msg || t("common.error"));
    } finally {
      setBusy(null); setConfirmOpen(false);
    }
  };

  const cancelOrder = async () => {
    setBusy("cancel"); setError("");
    try {
      const resp = await api.post<BrokerOrder>(`/users/me/orders/${order.id}/cancel`);
      onUpdated(resp.data);
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setError(msg || t("common.error"));
    } finally {
      setBusy(null);
    }
  };

  const statusColor = STATUS_COLORS[order.status] || "#888";
  const actionColor = ACTION_COLORS[order.action] || "#888";
  const isTerminal = ["filled", "rejected", "cancelled"].includes(order.status);

  const fmt = (v: string | null) => (v ? parseFloat(v).toFixed(2) : "—");

  return (
    <div style={card}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "0.75rem" }}>
        <div style={{ flex: 1 }}>
          <div style={{ display: "flex", gap: "0.4rem", flexWrap: "wrap", marginBottom: "0.3rem" }}>
            <span style={{ fontWeight: 700, fontSize: "1rem", color: "#1a1a2e" }}>{order.symbol}</span>
            <span style={badge(actionColor)}>{t(`trading.${order.action}`)}</span>
            <span style={badge(statusColor)}>{t(`trading.${order.status}`)}</span>
            <span style={badge("#5c6bc0")}>{order.mode}</span>
          </div>

          <div style={{ display: "flex", gap: "1.25rem", fontSize: "0.82rem", color: "#666", flexWrap: "wrap" }}>
            <span>{t("trading.size")}: <strong>{parseFloat(order.size).toFixed(0)}</strong></span>
            <span>{t("trading.entry")}: <strong>{fmt(order.entry)}</strong></span>
            <span>{t("trading.target")}: <strong>{fmt(order.target)}</strong></span>
            <span>{t("trading.stop")}: <strong>{fmt(order.stop)}</strong></span>
          </div>

          {order.filled_price && (
            <div style={{ fontSize: "0.8rem", color: "#27ae60", marginTop: "0.2rem" }}>
              {t("trading.filledAt")}: <strong>${parseFloat(order.filled_price).toFixed(2)}</strong>
            </div>
          )}
          {order.notes && (
            <p style={{ margin: "0.35rem 0 0", fontSize: "0.78rem", color: "#888", fontStyle: "italic" }}>"{order.notes}"</p>
          )}
          <div style={{ fontSize: "0.7rem", color: "#bbb", marginTop: "0.25rem" }}>
            {new Date(order.created_at).toLocaleDateString()}
            {order.confirmed_at && ` · ${t("trading.confirmedAt")}: ${new Date(order.confirmed_at).toLocaleDateString()}`}
            {order.alpaca_order_id && (
              <span style={{ marginLeft: "0.5rem" }}>{t("trading.alpacaId")}: {order.alpaca_order_id.slice(0, 8)}…</span>
            )}
          </div>
        </div>

        {/* Actions */}
        {!isTerminal && (
          <div style={{ display: "flex", flexDirection: "column", gap: "0.35rem", alignItems: "flex-end" }}>
            {order.status === "proposed" && (
              <button
                style={btn("#1565c0")}
                disabled={!!busy}
                onClick={() => setConfirmOpen(true)}
              >
                {busy === "confirm" ? "…" : t("trading.confirmBtn")}
              </button>
            )}
            {order.status === "confirmed" && (
              <button
                style={btn("#e67e22")}
                disabled={!!busy}
                onClick={() => act("submit", "submit")}
              >
                {busy === "submit" ? "…" : t("trading.submitBtn")}
              </button>
            )}
            {order.status === "submitted" && (
              <button
                style={outlineBtn}
                disabled={!!busy}
                onClick={() => act("refresh", "refresh")}
              >
                {busy === "refresh" ? "…" : t("trading.refreshBtn")}
              </button>
            )}
            <button
              style={{ ...btn("#c0392b11", "#c0392b"), border: "1px solid #c0392b33" }}
              disabled={!!busy}
              onClick={cancelOrder}
            >
              {busy === "cancel" ? "…" : t("trading.cancelBtn")}
            </button>
          </div>
        )}
      </div>

      {error && <p style={{ color: "#c0392b", fontSize: "0.78rem", margin: "0.35rem 0 0" }}>{error}</p>}

      {/* Confirm dialog */}
      {confirmOpen && (
        <div style={{ marginTop: "0.75rem", padding: "0.75rem", background: "#fffde7", border: "1px solid #ffe082", borderRadius: "6px" }}>
          <p style={{ margin: "0 0 0.5rem", fontSize: "0.83rem", color: "#555" }}>{t("trading.confirmWarning")}</p>
          <div style={{ display: "flex", gap: "0.5rem" }}>
            <button style={btn("#1565c0")} disabled={!!busy} onClick={() => act("confirm", "confirm")}>
              {busy === "confirm" ? "…" : t("trading.confirmBtn")}
            </button>
            <button style={outlineBtn} onClick={() => setConfirmOpen(false)}>{t("common.cancel")}</button>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Score button ──────────────────────────────────────────────────────────────

function ScoreButton({ t, onScored }: { t: (k: string) => string; onScored: () => void }) {
  const [scoring, setScoring] = useState(false);
  const [msg, setMsg] = useState("");

  const score = async () => {
    setScoring(true); setMsg("");
    try {
      await api.post("/users/me/journal/score");
      setMsg(t("trading.scoringStarted"));
      setTimeout(() => { onScored(); setMsg(""); }, 4000);
    } finally {
      setScoring(false);
    }
  };

  return (
    <div style={{ display: "flex", alignItems: "center", gap: "0.6rem" }}>
      {msg && <span style={{ fontSize: "0.78rem", color: "#27ae60" }}>{msg}</span>}
      <button onClick={score} disabled={scoring} style={{ ...outlineBtn, opacity: scoring ? 0.65 : 1 }}>
        {scoring ? t("trading.scoring") : t("trading.scoreMyTrades")}
      </button>
    </div>
  );
}

// ── Journal entry card (with close-trade UI) ──────────────────────────────────

const OUTCOME_COLORS: Record<string, string> = {
  won: "#27ae60", lost: "#c0392b", open: "#1565c0",
  expired: "#888", inconclusive: "#aaa",
};

function JournalCard({ entry, t, onUpdated }: { entry: JournalEntry; t: (k: string) => string; onUpdated: (e: JournalEntry) => void }) {
  const [closing, setClosing] = useState(false);
  const [exitVal, setExitVal] = useState(entry.exit ? parseFloat(entry.exit).toFixed(2) : "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const saveExit = async () => {
    if (!exitVal) return;
    setSaving(true); setError("");
    try {
      const resp = await api.patch<JournalEntry>(`/users/me/journal/${entry.id}`, { exit: parseFloat(exitVal) });
      onUpdated(resp.data);
      setClosing(false);
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setError(msg || t("common.error"));
    } finally {
      setSaving(false);
    }
  };

  const outcomeColor = entry.outcome ? (OUTCOME_COLORS[entry.outcome] || "#888") : "#bbb";
  const outcomeKey = entry.outcome ? `trading.outcome${entry.outcome.charAt(0).toUpperCase() + entry.outcome.slice(1)}` : null;

  return (
    <div style={card}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "0.75rem" }}>
        <div style={{ flex: 1 }}>
          <div style={{ display: "flex", gap: "0.4rem", flexWrap: "wrap", marginBottom: "0.3rem" }}>
            <span style={{ fontWeight: 700, color: "#1a1a2e" }}>{entry.symbol}</span>
            <span style={badge(ACTION_COLORS[entry.action] || "#888")}>{t(`trading.${entry.action}`)}</span>
            <span style={badge("#5c6bc0")}>{entry.mode}</span>
            {outcomeKey && <span style={badge(outcomeColor)}>{t(outcomeKey)}</span>}
          </div>

          <div style={{ display: "flex", gap: "1.25rem", fontSize: "0.82rem", color: "#666", flexWrap: "wrap" }}>
            <span>{t("trading.journalEntry")}: <strong>${parseFloat(entry.entry).toFixed(2)}</strong></span>
            {entry.exit && <span>{t("trading.journalExit")}: <strong>${parseFloat(entry.exit).toFixed(2)}</strong></span>}
            <span>{t("trading.size")}: <strong>{parseFloat(entry.size).toFixed(0)}</strong></span>
            {entry.target_price && <span>{t("execution.target")}: <strong>${parseFloat(entry.target_price).toFixed(2)}</strong></span>}
            {entry.stop_price && <span>{t("execution.stop")}: <strong>${parseFloat(entry.stop_price).toFixed(2)}</strong></span>}
          </div>

          {(entry.max_adverse_excursion || entry.max_favorable_excursion) && (
            <div style={{ fontSize: "0.72rem", color: "#aaa", marginTop: "0.15rem" }}>
              {entry.max_adverse_excursion && <span style={{ marginRight: "0.75rem" }}>{t("trading.mae")}: {parseFloat(entry.max_adverse_excursion).toFixed(2)}</span>}
              {entry.max_favorable_excursion && <span>{t("trading.mfe")}: {parseFloat(entry.max_favorable_excursion).toFixed(2)}</span>}
            </div>
          )}
          <div style={{ fontSize: "0.7rem", color: "#bbb", marginTop: "0.2rem" }}>
            {new Date(entry.timestamp).toLocaleString()}
          </div>
        </div>

        {!entry.exit && (
          <button onClick={() => setClosing((v) => !v)} style={outlineBtn}>
            {t("trading.closeTrade")}
          </button>
        )}
      </div>

      {closing && (
        <div style={{ marginTop: "0.6rem", display: "flex", gap: "0.5rem", alignItems: "center" }}>
          <input
            type="number" step="0.01" min="0"
            value={exitVal}
            onChange={(e) => setExitVal(e.target.value)}
            placeholder={t("trading.exitPrice")}
            style={{ ...inp, width: "120px" }}
          />
          <button onClick={saveExit} disabled={saving} style={{ ...btn("#27ae60"), opacity: saving ? 0.65 : 1 }}>
            {saving ? t("trading.savingExit") : t("trading.saveExit")}
          </button>
          <button onClick={() => setClosing(false)} style={outlineBtn}>{t("trading.cancelClose")}</button>
          {error && <span style={{ fontSize: "0.78rem", color: "#c0392b" }}>{error}</span>}
        </div>
      )}
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

const FILTER_STATUSES = ["all", "proposed", "confirmed", "submitted", "filled", "rejected", "cancelled"] as const;

export function PaperTrading() {
  const { t } = useTranslation();
  const [searchParams] = useSearchParams();

  const [orders, setOrders] = useState<BrokerOrder[]>([]);
  const [journal, setJournal] = useState<JournalEntry[]>([]);
  const [filter, setFilter] = useState<string>("all");
  const [showModal, setShowModal] = useState(false);
  const [modalInit, setModalInit] = useState<ProposeModalProps["initial"]>({});

  const loadOrders = useCallback(async () => {
    const resp = await api.get<BrokerOrder[]>("/users/me/orders");
    setOrders(resp.data);
  }, []);

  const loadJournal = useCallback(async () => {
    const resp = await api.get<JournalEntry[]>("/users/me/journal");
    setJournal(resp.data);
  }, []);

  useEffect(() => {
    loadOrders();
    loadJournal();

    // Auto-open modal with pre-filled values from query params (from StrategyCardDetail)
    const symbol = searchParams.get("symbol");
    const action = searchParams.get("action");
    const entry = searchParams.get("entry");
    const target = searchParams.get("target");
    const stop = searchParams.get("stop");
    const cardId = searchParams.get("cardId");
    if (symbol) {
      setModalInit({ symbol: symbol || undefined, action: action || undefined, entry: entry || undefined, target: target || undefined, stop: stop || undefined, cardId: cardId || undefined });
      setShowModal(true);
    }
  }, []);

  const onOrderCreated = (order: BrokerOrder) => {
    setOrders((prev) => [order, ...prev]);
    setShowModal(false);
  };

  const onOrderUpdated = (updated: BrokerOrder) => {
    setOrders((prev) => prev.map((o) => (o.id === updated.id ? updated : o)));
    if (updated.status === "filled") loadJournal();
  };

  const displayed = filter === "all" ? orders : orders.filter((o) => o.status === filter);

  const tabBtn = (val: string): React.CSSProperties => ({
    padding: "0.3rem 0.85rem", borderRadius: "20px", fontSize: "0.8rem", cursor: "pointer",
    fontWeight: filter === val ? 700 : 400,
    background: filter === val ? "#1a1a2e" : "transparent",
    color: filter === val ? "#fff" : "#555",
    border: filter === val ? "none" : "1px solid #ddd",
  });

  return (
    <div style={{ minHeight: "100vh", background: "#f5f5f5", fontFamily: "system-ui, sans-serif" }}>
      <NavBar />
      <main style={{ maxWidth: "860px", margin: "0 auto", padding: "2rem 1.5rem" }}>

        {/* Header */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1.25rem" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
            <h1 style={{ margin: 0, fontSize: "1.4rem", color: "#1a1a2e" }}>{t("trading.title")}</h1>
            <span style={badge("#5c6bc0")}>{t("trading.paperBadge")}</span>
          </div>
          <button
            onClick={() => { setModalInit({}); setShowModal(true); }}
            style={{ padding: "0.5rem 1.1rem", background: "#1a1a2e", color: "#fff", border: "none", borderRadius: "4px", cursor: "pointer", fontWeight: 600, fontSize: "0.875rem" }}
          >
            + {t("trading.newOrder")}
          </button>
        </div>

        {/* Status filter tabs */}
        <div style={{ display: "flex", gap: "0.35rem", flexWrap: "wrap", marginBottom: "1.1rem" }}>
          {FILTER_STATUSES.map((s) => (
            <button key={s} style={tabBtn(s)} onClick={() => setFilter(s)}>
              {s === "all" ? t("trading.filterAll") : t(`trading.${s}`)}
            </button>
          ))}
        </div>

        {/* Orders list */}
        {displayed.length === 0 ? (
          <div style={{ background: "#fff", border: "1px solid #e0e0e0", borderRadius: "8px", padding: "1.5rem", color: "#888", fontStyle: "italic", marginBottom: "1.5rem" }}>
            {t("trading.noOrders")}
          </div>
        ) : (
          <div style={{ marginBottom: "1.5rem" }}>
            {displayed.map((order) => (
              <OrderCard key={order.id} order={order} t={t} onUpdated={onOrderUpdated} />
            ))}
          </div>
        )}

        {/* Journal */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.75rem" }}>
          <h2 style={{ fontSize: "1.1rem", color: "#1a1a2e", margin: 0 }}>{t("trading.journal")}</h2>
          {journal.length > 0 && (
            <ScoreButton t={t} onScored={loadJournal} />
          )}
        </div>
        {journal.length === 0 ? (
          <div style={{ background: "#fff", border: "1px solid #e0e0e0", borderRadius: "8px", padding: "1.25rem", color: "#aaa", fontStyle: "italic" }}>
            {t("trading.noJournal")}
          </div>
        ) : (
          journal.map((je) => (
            <JournalCard key={je.id} entry={je} t={t} onUpdated={(updated) => setJournal((prev) => prev.map((j) => j.id === updated.id ? updated : j))} />
          ))
        )}
      </main>

      {showModal && (
        <ProposeModal
          t={t}
          initial={modalInit}
          onClose={() => setShowModal(false)}
          onCreated={onOrderCreated}
        />
      )}
    </div>
  );
}
