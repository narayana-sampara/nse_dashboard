"use client";

export type MLFuturePrediction = {
  rank: number;
  symbol: string;
  name: string;
  sector: string;
  current_price: number;
  target_price_1y: number;
  implied_cagr_pct: number;
  probability_positive: number;
  conviction: "HIGH" | "MEDIUM" | "SPECULATIVE";
  shap_values: Record<string, number>;
  dynamic_thesis: string;
};

function formatCurrency(value: number): string {
  return `Rs ${value.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;
}

function formatPercent(value: number, digits = 1): string {
  return `${value > 0 ? "+" : ""}${value.toLocaleString("en-IN", {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  })}%`;
}

function labelFeature(name: string): string {
  return name.replaceAll("_", " ");
}

export default function MLFutureCard({
  stock,
  expanded,
  onToggle,
}: {
  stock: MLFuturePrediction;
  expanded: boolean;
  onToggle: () => void;
}) {
  const convictionClass = stock.conviction.toLowerCase();
  const shapEntries = Object.entries(stock.shap_values ?? {})
    .sort((left, right) => Math.abs(right[1]) - Math.abs(left[1]))
    .slice(0, 8);
  const maxAbs = Math.max(...shapEntries.map(([, value]) => Math.abs(value)), 1);

  return (
    <article className={`future-card conviction-${convictionClass}`}>
      <button className="future-summary ml-future-summary" onClick={onToggle} aria-expanded={expanded}>
        <span className="future-rank">{stock.rank}</span>
        <span className="future-name"><b>{stock.name}</b><small>{stock.symbol} | {stock.sector}</small></span>
        <span><small>Current price</small><b>{formatCurrency(stock.current_price)}</b></span>
        <span><small>1Y target</small><b className="positive">{formatCurrency(stock.target_price_1y)}</b></span>
        <span className={stock.implied_cagr_pct >= 0 ? "positive" : "negative"}><small>Forecast return</small><b>{formatPercent(stock.implied_cagr_pct, 0)}</b></span>
        <span><small>Probability positive</small><b>{Math.round(stock.probability_positive * 100)}%</b></span>
        <mark className={`conviction-badge ${convictionClass}`}>{stock.conviction}</mark>
      </button>
      {expanded && (
        <div className="future-detail ml-future-detail">
          <div className="future-thesis-panel">
            <h3>Model thesis</h3>
            <p className="future-thesis">{stock.dynamic_thesis}</p>
            <div className="future-metrics-grid">
              <div><small>Rank</small><b>#{stock.rank}</b></div>
              <div><small>Conviction</small><b>{stock.conviction}</b></div>
              <div><small>Current</small><b>{formatCurrency(stock.current_price)}</b></div>
              <div><small>Target</small><b>{formatCurrency(stock.target_price_1y)}</b></div>
            </div>
          </div>
          <div className="future-metrics-panel shap-panel">
            <h3>Top SHAP drivers</h3>
            {shapEntries.length ? shapEntries.map(([name, value]) => (
              <div className="shap-row" key={name}>
                <span>{labelFeature(name)}</span>
                <i><em className={value >= 0 ? "positive-fill" : "negative-fill"} style={{ width: `${Math.max(8, Math.abs(value) / maxAbs * 100)}%` }} /></i>
                <b className={value >= 0 ? "positive" : "negative"}>{value > 0 ? "+" : ""}{value.toFixed(2)}</b>
              </div>
            )) : <div className="empty">No SHAP values were returned for this prediction.</div>}
          </div>
        </div>
      )}
    </article>
  );
}
