import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { CandlestickSeries, createChart, createSeriesMarkers, type SeriesMarker, type Time } from "lightweight-charts";
import api from "../api/client";
import { OUTCOME_COLORS } from "./outcomeColors";

type Candle = { time: string; open: number; high: number; low: number; close: number };

/** How the idea's outcome check resolved it, on the days it looked at (#112). */
type ChartedOutcome = {
  outcome: string;
  outcome_date: string | null;
  window_start: string;
  window_end: string;
  worst_price: number | null;
  best_price: number | null;
};

/** A trade idea's chart as the API serves it (#109). */
type IdeaChart = {
  symbol: string;
  action: string | null;
  proposed_on: string;
  entry: number | null;
  target: number | null;
  stop: number | null;
  candles: Candle[];
  priced_as: string;
  stands_in_for_spot: boolean;
  outcome: ChartedOutcome | null;
  no_outcome_because: "not_checked" | "inconclusive" | null;
};

const LEVEL_COLORS = { entry: "#1565c0", target: "#27ae60", stop: "#c0392b" } as const;
const EXCURSION_COLOR = "#e67e22";

/** The first day with a bar on or after `day`: a weekend or a holiday has none of its own. */
function barOnOrAfter(candles: Candle[], day: string): Candle | undefined {
  return candles.find((c) => c.time >= day);
}

/**
 * Decimals enough for about six significant digits at the chart's smallest
 * price: 1.0850 and 1.0800 on EURUSD are a trade apart, and the library's
 * default of two would print both as 1.08. Never fewer than two, never more
 * than eight.
 */
function pricePrecision(chart: IdeaChart): number {
  const prices = [
    ...chart.candles.map((c) => c.low),
    chart.entry, chart.target, chart.stop,
  ].filter((p): p is number => p !== null && p > 0);
  if (prices.length === 0) return 2;
  const magnitude = Math.floor(Math.log10(Math.min(...prices)));
  return Math.min(8, Math.max(2, 5 - magnitude));
}

/**
 * A trade idea in daily bars around the day it was proposed, with its entry,
 * target and stop drawn across it.
 */
export function TradeIdeaChart({ workspaceId, ideaId }: { workspaceId: string; ideaId: string }) {
  const { t } = useTranslation();
  const [chart, setChart] = useState<IdeaChart | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api
      .get<IdeaChart>(`/workspaces/${workspaceId}/trade-ideas/${ideaId}/chart`)
      .then(({ data }) => { if (!cancelled) setChart(data); })
      .catch(() => { if (!cancelled) setFailed(true); });
    return () => { cancelled = true; };
  }, [workspaceId, ideaId]);

  if (failed) return <p style={note}>{t("tradeChart.failed")}</p>;
  if (!chart) return <p style={note}>{t("tradeChart.loading")}</p>;
  if (chart.candles.length === 0) return <p style={note}>{t("tradeChart.noPrices", { symbol: chart.symbol })}</p>;
  return <Candles chart={chart} />;
}

function Candles({ chart }: { chart: IdeaChart }) {
  const { t } = useTranslation();
  const container = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const element = container.current;
    if (!element) return;

    const drawn = createChart(element, {
      height: 300,
      autoSize: true,
      layout: { background: { color: "#ffffff" }, textColor: "#555" },
      grid: { vertLines: { color: "#f0f0f0" }, horzLines: { color: "#f0f0f0" } },
      timeScale: { borderColor: "#e0e0e0" },
      rightPriceScale: { borderColor: "#e0e0e0" },
    });
    const precision = pricePrecision(chart);
    const series = drawn.addSeries(CandlestickSeries, {
      upColor: "#26a69a", downColor: "#ef5350", borderVisible: false,
      wickUpColor: "#26a69a", wickDownColor: "#ef5350",
      priceFormat: { type: "price", precision, minMove: 10 ** -precision },
    });
    series.setData(chart.candles.map((c) => ({ ...c, time: c.time as Time })));

    for (const level of ["entry", "target", "stop"] as const) {
      const price = chart[level];
      if (price === null) continue;
      series.createPriceLine({
        price, color: LEVEL_COLORS[level], lineWidth: 2, lineStyle: 2,
        axisLabelVisible: true, title: t(`tradeChart.${level}`),
      });
    }

    const markers: SeriesMarker<Time>[] = [];
    const short = chart.action === "short";
    const proposed = barOnOrAfter(chart.candles, chart.proposed_on);
    if (proposed) {
      markers.push({
        time: proposed.time as Time,
        position: short ? "aboveBar" : "belowBar",
        shape: short ? "arrowDown" : "arrowUp",
        color: LEVEL_COLORS.entry,
        text: t("tradeChart.proposed"),
      });
    }

    const outcome = chart.outcome;
    if (outcome) {
      // The day the target or stop was hit, in the badge's colour.
      const resolved = outcome.outcome_date ? barOnOrAfter(chart.candles, outcome.outcome_date) : undefined;
      if (resolved) {
        markers.push({
          time: resolved.time as Time,
          position: "aboveBar",
          shape: "circle",
          color: OUTCOME_COLORS[outcome.outcome] || "#888",
          text: t(`detail.${outcome.outcome}`),
        });
      }
      // Where the days the check looked at end: the last bar inside them.
      const inside = chart.candles.filter((c) => c.time <= outcome.window_end);
      const windowEnd = inside[inside.length - 1];
      if (windowEnd && windowEnd !== resolved) {
        markers.push({
          time: windowEnd.time as Time,
          position: "belowBar",
          shape: "square",
          color: OUTCOME_COLORS[outcome.outcome] || "#888",
          // An expired trade resolved here, by running out of days.
          text: outcome.outcome === "expired" ? t("detail.expired") : t("tradeChart.windowEnds"),
        });
      }
      for (const [price, key] of [[outcome.worst_price, "worst"], [outcome.best_price, "best"]] as const) {
        if (price === null) continue;
        series.createPriceLine({
          price, color: EXCURSION_COLOR, lineWidth: 1, lineStyle: 1,
          axisLabelVisible: false, title: t(`tradeChart.${key}`),
        });
      }
    }
    // The library draws markers in time order.
    markers.sort((a, b) => String(a.time).localeCompare(String(b.time)));
    createSeriesMarkers(series, markers);

    drawn.timeScale().fitContent();
    return () => drawn.remove();
  }, [chart, t]);

  return (
    <div style={{ marginBottom: "0.4rem" }}>
      <p style={{ ...note, margin: "0 0 0.3rem" }}>{t("tradeChart.caption", { symbol: chart.symbol })}</p>
      {chart.outcome && (
        <p style={{ ...note, margin: "0 0 0.3rem" }}>
          {t("tradeChart.window", { start: chart.outcome.window_start, end: chart.outcome.window_end })}
        </p>
      )}
      {chart.no_outcome_because && (
        <p style={{ ...note, margin: "0 0 0.3rem" }}>{t(`tradeChart.${chart.no_outcome_because}`)}</p>
      )}
      {chart.stands_in_for_spot && (
        <p style={{ ...note, margin: "0 0 0.3rem", color: "#e67e22" }}>
          {t("tradeChart.standsIn", { pricedAs: chart.priced_as })}
        </p>
      )}
      <div ref={container} style={{ width: "100%", height: "300px" }} />
    </div>
  );
}

const note = { fontSize: "0.78rem", color: "#888", margin: "0.3rem 0" };
