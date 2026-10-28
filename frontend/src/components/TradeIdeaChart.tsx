import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { CandlestickSeries, createChart, createSeriesMarkers, type Time } from "lightweight-charts";
import api from "../api/client";

type Candle = { time: string; open: number; high: number; low: number; close: number };

/** A trade idea's chart as the API serves it (#109). */
type IdeaChart = {
  symbol: string;
  action: string | null;
  proposed_on: string;
  entry: number | null;
  target: number | null;
  stop: number | null;
  candles: Candle[];
};

const LEVEL_COLORS = { entry: "#1565c0", target: "#27ae60", stop: "#c0392b" } as const;

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

    // On the first day with a bar from the day it was proposed: a weekend or a
    // holiday has none of its own.
    const proposed = chart.candles.find((c) => c.time >= chart.proposed_on);
    if (proposed) {
      const short = chart.action === "short";
      createSeriesMarkers(series, [{
        time: proposed.time as Time,
        position: short ? "aboveBar" : "belowBar",
        shape: short ? "arrowDown" : "arrowUp",
        color: LEVEL_COLORS.entry,
        text: t("tradeChart.proposed"),
      }]);
    }

    drawn.timeScale().fitContent();
    return () => drawn.remove();
  }, [chart, t]);

  return (
    <div style={{ marginBottom: "0.4rem" }}>
      <p style={{ ...note, margin: "0 0 0.3rem" }}>{t("tradeChart.caption", { symbol: chart.symbol })}</p>
      <div ref={container} style={{ width: "100%", height: "300px" }} />
    </div>
  );
}

const note = { fontSize: "0.78rem", color: "#888", margin: "0.3rem 0" };
