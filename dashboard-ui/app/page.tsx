"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import MLFutureCard, { type MLFuturePrediction } from "../components/MLFutureCard";
import { apiFetch, fetchCurrentUser, getToken, logout, type CurrentUser, type MenuKey } from "../lib/auth";

const MENU_ITEMS: { key: Exclude<MenuKey, "five_percent_strategy">; label: string }[] = [
  { key: "signals", label: "Signals" },
  { key: "weekly", label: "Weekly Predictions" },
  { key: "monthly", label: "Monthly Predictions" },
  { key: "future", label: "Future Stocks" },
  { key: "analysis", label: "Deep Dive" },
];

type Stock = { symbol: string; name: string; price: number; change_pct: number; score: number; signal: string };
type Sector = { name: string; scanned: number; buys: Stock[]; sells: Stock[] };
type GenerationStatus = { state: string; message?: string; task_id?: string } | string;
type Dashboard = { generated_at: string | null; market_date?: string | null; stocks_scored: number; universe_size: number; sectors: Sector[]; regime?: { state: string; maximum_exposure_pct: number; risk_per_trade_pct: number }; generation_status?: GenerationStatus };
type PaperPortfolio = { summary: { equity: number; drawdown_pct: number; open_positions: number; new_entries_allowed: boolean } };
type Alert = { created_at: string; symbol: string; sector?: string; signal: string; score: number; price?: number };
type Indicator = {
  signal: "BUY" | "SELL" | "HOLD"; strength_score: number; as_of: string;
  features: { macd?: number; macd_signal?: number; adx_14?: number; positive_di_14?: number; negative_di_14?: number };
};
type WeeklyPick = {
  symbol: string; name: string; sector: string; price: number; sector_rank: number;
  predicted_5d_return_pct: number; target_probability: number; ranking_score: number;
  risk_score: number; reasons: string[];
  buy_rank?: number; sell_rank?: number; indicator: Indicator;
};
type WeeklySector = { name: string; picks: WeeklyPick[]; buys: WeeklyPick[]; sells: WeeklyPick[] };
type WeeklyPredictions = {
  generated_at: string | null; market_date: string | null; valid_until: string | null;
  predictions_count: number; eligible_stocks?: number; universe_size?: number;
  buy_count?: number; sell_count?: number;
  model: { name: string; version: string } | null; sectors: WeeklySector[]; disclaimer?: string;
  generation_status?: GenerationStatus;
};
type MonthlyPick = {
  symbol: string; name: string; sector: string; price: number; sector_rank: number;
  horizon_months: number; predicted_return_pct: number; target_probability: number;
  score: number; risk_score: number; reasons?: string[]; state?: "WATCHLIST" | "BUY_READY";
  nifty_50_member?: boolean; rejection_reasons?: string[];
  entry?: { price?: number; proposed_stop?: number; quantity?: number; estimated_risk?: number };
  score_breakdown?: Record<string, number>;
  buy_rank?: number; sell_rank?: number; indicator: Indicator;
};
type MonthlySector = { name: string; picks: MonthlyPick[]; buys: MonthlyPick[]; sells: MonthlyPick[] };
type MonthlyPredictions = {
  generated_at: string | null; market_date: string | null; horizon_months: number;
  predictions_count: number; eligible_stocks?: number; universe_size?: number;
  buy_count?: number; sell_count?: number;
  model: { name: string; version: string } | null; sectors: MonthlySector[];
  score_method: Record<string, number>; disclaimer?: string; regime?: { state: string; maximum_exposure_pct: number; risk_per_trade_pct: number };
  generation_status?: GenerationStatus;
};
type DeepDiveReturn = { median: number | null; lower: number | null; upper: number | null; hit_rate: number | null };
type DeepDiveAnalysis = {
  generated_at: string; symbol: string; name: string; sector: string; source: string;
  price?: number; as_of?: string; requested_horizon: string;
  overall_signal: "STRONG_BUY" | "BUY" | "HOLD" | "SELL" | "STRONG_SELL";
  overall_score: number; confidence_interval: "High" | "Medium" | "Low";
  score_contributions: Record<string, number>;
  factor_breakdown: {
    technical: {
      score: number; coverage: string; condition_flags: string[];
      trend: { display: string; ema_20?: number; ema_50?: number; ema_200?: number; adx_14?: number };
      momentum: { display: string; rsi_14?: number; macd_histogram_slope?: number };
      volatility: { display: string; atr_14?: number; average_atr_50?: number };
      price_vs_sma20: { display: string; value_pct: number };
    };
    fundamental: {
      score: number; coverage: string;
      valuation: { display: string; pe_ratio?: number | null; sector_pe_ratio?: number | null; pb_ratio?: number | null };
      quality: { display: string; roe_pct?: number | null; roce_pct?: number | null; debt_to_equity?: number | null };
      growth: { display: string; ttm_revenue_growth_pct?: number | null; qoq_profit_growth_pct?: number | null };
    };
    smart_money_options: {
      score: number; coverage: string; display: string;
      oi_change?: number | null; pcr?: number | null; iv_skew?: number | null; gex?: number | null;
    };
    news_sentiment_legal: {
      score: number; coverage: string; display: string;
      sentiment_score?: number | null; finbert_score?: number | null; legal_risk?: string; legal_risk_quotient?: number | null;
    };
  };
  projected_returns: {
    sample_size: number; score_matched: number; warnings?: string[];
    horizon_5d: DeepDiveReturn; horizon_15d: DeepDiveReturn; horizon_30d: DeepDiveReturn;
  };
  data_warnings: string[]; methodology?: { projection?: string; guardrail?: string }; disclaimer: string;
};
type ProjectionPoint = {
  fiscal_year: number; price: number; year_growth_pct: number; cumulative_growth_pct: number;
  revenue: number; ebitda_margin_pct: number; eps?: number | null; valuation_multiple: number;
};
type ProjectionYear = { fiscal_year: number; bear: ProjectionPoint; base: ProjectionPoint; bull: ProjectionPoint };
type GrowthCandidate = {
  rank: number; symbol: string; name: string; sector: string; as_of: string;
  signal_date: string; signal_price: number; current_price: number; return_since_signal_pct: number;
  strength_score: number; confidence_pct: number; state: string; penalty: number;
  algorithm_scores: Record<string, number>; risk_flags: string[];
  track_eligibility: { compounder_12m: boolean; multibagger_24m: boolean };
  evidence: { title?: string; source?: string; source_url?: string; published_at?: string }[];
  data_freshness: string;
  projections: {
    available: boolean; reason?: string; years: ProjectionYear[];
    implied_cagr_pct?: Record<string, number>; assumptions?: Record<string, number>;
  };
};
type GrowthRadar = {
  generated_at: string | null; market_date: string | null; universe_size: number;
  eligible_stocks: number; candidates: GrowthCandidate[]; disclaimer: string;
};
type FuturePrice = {
  symbol: string; name: string; currency: string; price: number; close: number; change: number; change_pct: number;
  previous_close: number | null; day_high: number | null; day_low: number | null; as_of: string;
  market_state: string; price_basis: "TODAY_CLOSE" | "INTRADAY" | "LATEST";
};
type MLForwardReturns = {
  generated_at: string | null;
  model_version: string;
  predictions: MLFuturePrediction[];
  predictions_count?: number;
  universe_size?: number;
  generation_status?: GenerationStatus;
  disclaimer?: string;
};
type StreamMessage = { type: string; data?: unknown };

function sortAlertsByScore(alerts: Alert[]): Alert[] {
  return [...alerts].sort((left, right) =>
    right.score - left.score
    || new Date(right.created_at).getTime() - new Date(left.created_at).getTime()
  );
}

function formatNumber(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return value.toLocaleString("en-IN", { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

function formatPercent(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${value > 0 ? "+" : ""}${value.toLocaleString("en-IN", { maximumFractionDigits: digits, minimumFractionDigits: digits })}%`;
}

function priceBasisLabel(price: FuturePrice | null): string {
  if (!price) return "Modeled price";
  if (price.price_basis === "TODAY_CLOSE") return "Today close";
  if (price.price_basis === "INTRADAY") return "Current price";
  return "Latest price";
}

function generationStatusMessage(payload: { generation_status?: GenerationStatus } | null | undefined): string {
  const status = payload?.generation_status;
  if (!status) return "";
  if (typeof status === "string") {
    return status === "queued" ? "Generation was queued. The dashboard will update when the worker finishes." : status;
  }
  return status.message ?? `Generation is ${status.state}.`;
}

export default function Home() {
  const router = useRouter();
  const [currentUser, setCurrentUser] = useState<CurrentUser | null>(null);
  const [authChecked, setAuthChecked] = useState(false);
  const [view, setView] = useState<"signals" | "weekly" | "monthly" | "radar" | "analysis" | "future">("signals");
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [weekly, setWeekly] = useState<WeeklyPredictions | null>(null);
  const [monthly, setMonthly] = useState<MonthlyPredictions | null>(null);
  const months = 1;
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [portfolio, setPortfolio] = useState<PaperPortfolio | null>(null);
  const [side, setSide] = useState<"buys" | "sells">("buys");
  const [sector, setSector] = useState("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [operationStatus, setOperationStatus] = useState("");
  const [stream, setStream] = useState<"live" | "offline" | "disabled">("disabled");
  const [analysisSymbol, setAnalysisSymbol] = useState("RELIANCE.NS");
  const [analysis, setAnalysis] = useState<DeepDiveAnalysis | null>(null);
  const [growthRadar, setGrowthRadar] = useState<GrowthRadar | null>(null);
  const [radarAlgorithm, setRadarAlgorithm] = useState("strength_score");
  const [radarStage, setRadarStage] = useState("all");
  const [radarTrack, setRadarTrack] = useState("all");
  const [expandedGrowth, setExpandedGrowth] = useState<string | null>(null);
  const [futureConviction, setFutureConviction] = useState("all");
  const [futureSector, setFutureSector] = useState("all");
  const [expandedFuture, setExpandedFuture] = useState<string | null>(null);
  const [futureForecast, setFutureForecast] = useState<MLForwardReturns | null>(null);
  const [futureLoading, setFutureLoading] = useState(false);
  const [livePrices, setLivePrices] = useState<Record<string, FuturePrice>>({});
  const [futureLookupSymbol, setFutureLookupSymbol] = useState("RELIANCE.NS");
  const [futureLookupPrice, setFutureLookupPrice] = useState<FuturePrice | null>(null);
  const [pricesLoading, setPricesLoading] = useState(false);
  const [pricesFetchedAt, setPricesFetchedAt] = useState<string | null>(null);
  const [pricesError, setPricesError] = useState("");
  const [bookmarkedSymbols, setBookmarkedSymbols] = useState<Set<string>>(new Set());

  const loadBookmarks = useCallback(async () => {
    try {
      const response = await apiFetch("/api/v1/bookmarks", { cache: "no-store" });
      if (!response.ok) return;
      const payload = await response.json() as { symbol: string }[];
      setBookmarkedSymbols(new Set(payload.map((item) => item.symbol)));
    } catch { /* non-fatal */ }
  }, []);

  const toggleBookmark = useCallback(async (symbol: string) => {
    const isBookmarked = bookmarkedSymbols.has(symbol);
    setBookmarkedSymbols((prev) => {
      const next = new Set(prev);
      if (isBookmarked) next.delete(symbol); else next.add(symbol);
      return next;
    });
    try {
      const response = await apiFetch(`/api/v1/bookmarks/${symbol}`, { method: isBookmarked ? "DELETE" : "POST" });
      if (!response.ok) throw new Error(`Bookmark API returned ${response.status}`);
    } catch {
      setBookmarkedSymbols((prev) => {
        const next = new Set(prev);
        if (isBookmarked) next.add(symbol); else next.delete(symbol);
        return next;
      });
    }
  }, [bookmarkedSymbols]);

  const loadSignals = useCallback(async (refresh = false) => {
    setLoading(true); setError(""); setOperationStatus("");
    try {
      const [scanResponse, alertResponse, portfolioResponse] = await Promise.all([
        apiFetch(`/api/v1/dashboard${refresh ? "?refresh=true" : ""}`, { cache: "no-store" }),
        apiFetch("/api/v1/alerts?limit=50", { cache: "no-store" }),
        apiFetch("/api/v1/paper-portfolio", { cache: "no-store" }),
      ]);
      if (!scanResponse.ok) throw new Error(`Signal API returned ${scanResponse.status}`);
      const payload = await scanResponse.json() as Dashboard;
      setDashboard(payload);
      setOperationStatus(generationStatusMessage(payload));
      if (alertResponse.ok) setAlerts(sortAlertsByScore(await alertResponse.json()));
      if (portfolioResponse.ok) setPortfolio(await portfolioResponse.json());
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Unable to load dashboard"); }
    finally { setLoading(false); }
  }, []);

  const loadWeekly = useCallback(async (generate = false) => {
    setLoading(true); setError(""); setOperationStatus("");
    try {
      const response = await apiFetch(
        generate ? "/api/v1/weekly-predictions/generate" : "/api/v1/weekly-predictions?limit_per_sector=5",
        { method: generate ? "POST" : "GET", cache: "no-store" },
      );
      if (!response.ok) throw new Error(`Weekly prediction API returned ${response.status}`);
      const payload = await response.json() as WeeklyPredictions;
      setWeekly(payload);
      setOperationStatus(generationStatusMessage(payload));
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Unable to load weekly predictions"); }
    finally { setLoading(false); }
  }, []);

  const loadMonthly = useCallback(async (horizon: number, generate = false) => {
    setLoading(true); setError(""); setOperationStatus("");
    try {
      const query = `horizon_months=${horizon}&limit_per_sector=5`;
      const response = await apiFetch(
        `/api/v1/monthly-predictions${generate ? "/generate" : ""}?${query}`,
        { method: generate ? "POST" : "GET", cache: "no-store" },
      );
      if (!response.ok) throw new Error(`Monthly prediction API returned ${response.status}`);
      const payload = await response.json() as MonthlyPredictions;
      setMonthly(payload);
      setOperationStatus(generationStatusMessage(payload));
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Unable to load monthly predictions"); }
    finally { setLoading(false); }
  }, []);

  const loadAnalysis = useCallback(async () => {
    const symbol = analysisSymbol.trim().toUpperCase();
    if (!symbol) return;
    setLoading(true); setError("");
    try {
      const response = await apiFetch(`/api/v1/analysis/stock/${encodeURIComponent(symbol)}?horizon=15d`, { cache: "no-store" });
      if (!response.ok) {
        const payload = await response.json().catch(() => null);
        throw new Error(payload?.detail ?? `Stock analysis API returned ${response.status}`);
      }
      setAnalysis(await response.json());
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Unable to analyze stock"); }
    finally { setLoading(false); }
  }, [analysisSymbol]);

  const loadGrowthRadar = useCallback(async (generate = false) => {
    setLoading(true); setError(""); setOperationStatus("");
    try {
      const response = await apiFetch(
        "/api/v1/growth-radar" + (generate ? "/generate" : ""),
        { method: generate ? "POST" : "GET", cache: "no-store" },
      );
      if (!response.ok) throw new Error(`Growth radar API returned ${response.status}`);
      const payload = await response.json();
      if (payload.candidates) setGrowthRadar(payload);
      if (generate && payload.generation_status === "queued") {
        setOperationStatus("Growth-radar generation was queued. The dashboard will update when the worker finishes.");
      }
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Unable to load growth radar"); }
    finally { setLoading(false); }
  }, []);

  const loadFutureForecast = useCallback(async (refresh = false) => {
    setFutureLoading(true); setPricesError(""); setOperationStatus("");
    try {
      const response = await apiFetch(
        `/api/v1/ml/forward-returns?limit=40${refresh ? "&refresh=true" : ""}`,
        { cache: "no-store" },
      );
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(payload.error ?? payload.detail ?? `ML forecast API returned ${response.status}`);
      }
      setFutureForecast(payload as MLForwardReturns);
      setOperationStatus(generationStatusMessage(payload as MLForwardReturns));
    } catch (cause) {
      setPricesError(cause instanceof Error ? cause.message : "Unable to load ML forecasts");
    }
    finally { setFutureLoading(false); }
  }, []);

  useEffect(() => {
    const state = futureForecast?.generation_status && typeof futureForecast.generation_status !== "string"
      ? futureForecast.generation_status.state
      : futureForecast?.generation_status;
    if (state !== "queued") return;
    const timer = setTimeout(() => void loadFutureForecast(false), 6000);
    return () => clearTimeout(timer);
  }, [futureForecast, loadFutureForecast]);

  const loadFuturePrices = useCallback(async (
    symbols: string[],
    mode: "basket" | "lookup" = "basket",
  ) => {
    setPricesLoading(true); setPricesError("");
    try {
      const query = new URLSearchParams({ symbols: symbols.join(",") });
      const response = await apiFetch(`/api/v1/stock-prices?${query.toString()}`, { cache: "no-store" });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(payload.error ?? payload.detail ?? `Price feed returned ${response.status}`);
      }
      if (payload.error) throw new Error(payload.error);
      const prices = (payload.prices ?? {}) as Record<string, FuturePrice>;
      if (mode === "basket") {
        setLivePrices(prices);
      } else {
        const firstSymbol = String(payload.symbols?.[0] ?? symbols[0]).toUpperCase();
        const quote = prices[firstSymbol] ?? Object.values(prices)[0] ?? null;
        setFutureLookupPrice(quote);
        if (!quote) throw new Error("Yahoo Finance returned no quote for that symbol");
      }
      setPricesFetchedAt(payload.fetched_at ?? null);
    } catch (cause) {
      if (mode === "lookup") setFutureLookupPrice(null);
      setPricesError(cause instanceof Error ? cause.message : "Unable to fetch prices");
    }
    finally { setPricesLoading(false); }
  }, []);

  const lookupFuturePrice = useCallback(async () => {
    const symbol = futureLookupSymbol.trim();
    if (!symbol) return;
    await loadFuturePrices([symbol], "lookup");
  }, [futureLookupSymbol, loadFuturePrices]);

  useEffect(() => {
    if (!getToken()) { router.replace("/login"); return; }
    void fetchCurrentUser().then((user) => {
      if (!user) { router.replace("/login"); return; }
      setCurrentUser(user);
      setAuthChecked(true);
    });
  }, [router]);
  const allowedMenus = useMemo(
    () => MENU_ITEMS.filter((item) => currentUser?.role === "admin" || currentUser?.permissions.includes(item.key)),
    [currentUser]
  );
  useEffect(() => {
    if (authChecked && allowedMenus.length && !allowedMenus.some((item) => item.key === view)) {
      setView(allowedMenus[0].key);
    }
  }, [authChecked, allowedMenus, view]);
  const authReady = authChecked && !!currentUser;
  useEffect(() => { if (authReady) void loadSignals(); }, [authReady, loadSignals]);
  useEffect(() => { if (authReady) void loadBookmarks(); }, [authReady, loadBookmarks]);
  useEffect(() => { if (authReady && view === "weekly" && weekly === null) void loadWeekly(); }, [authReady, view, weekly, loadWeekly]);
  useEffect(() => { if (authReady && view === "monthly") void loadMonthly(months); }, [authReady, view, months, loadMonthly]);
  useEffect(() => { if (authReady && view === "radar" && growthRadar === null) void loadGrowthRadar(); }, [authReady, view, growthRadar, loadGrowthRadar]);
  useEffect(() => { if (authReady && view === "future" && futureForecast === null) void loadFutureForecast(); }, [authReady, view, futureForecast, loadFutureForecast]);
  useEffect(() => {
    const token = process.env.NEXT_PUBLIC_WEBSOCKET_TOKEN;
    const origin = process.env.NEXT_PUBLIC_API_ORIGIN;
    if (!token || !origin) return;
    const socket = new WebSocket(`${origin.replace(/^http/, "ws")}/api/v1/stream/alerts?token=${encodeURIComponent(token)}`);
    socket.onopen = () => setStream("live"); socket.onerror = () => setStream("offline"); socket.onclose = () => setStream("offline");
    socket.onmessage = async (event) => {
      const message = JSON.parse(event.data) as StreamMessage;
      if (message.type !== "heartbeat") {
        const response = await apiFetch("/api/v1/alerts?limit=50", { cache: "no-store" });
        if (response.ok) setAlerts(sortAlertsByScore(await response.json()));
      }
    };
    return () => socket.close();
  }, []);
  useEffect(() => {
    const token = process.env.NEXT_PUBLIC_WEBSOCKET_TOKEN;
    const origin = process.env.NEXT_PUBLIC_API_ORIGIN;
    if (!token || !origin) return;
    const socket = new WebSocket(`${origin.replace(/^http/, "ws")}/api/v1/stream/signals?token=${encodeURIComponent(token)}`);
    socket.onopen = () => setStream("live"); socket.onerror = () => setStream("offline"); socket.onclose = () => setStream("offline");
    socket.onmessage = async (event) => {
      const message = JSON.parse(event.data) as StreamMessage;
      if (message.type === "signals.updated") {
        setDashboard(message.data as Dashboard);
        setOperationStatus("");
      }
      if (message.type === "weekly_predictions.updated") {
        setWeekly(message.data as WeeklyPredictions);
        setOperationStatus("");
      }
      if (message.type === "monthly_predictions.updated") {
        setMonthly(message.data as MonthlyPredictions);
        setOperationStatus("");
      }
      if (message.type === "growth_radar.updated") {
        setGrowthRadar(message.data as GrowthRadar);
        setOperationStatus("");
      }
      if (message.type === "ml_predictions.updated") {
        setFutureForecast(message.data as MLForwardReturns);
        setOperationStatus("");
      }
    };
    return () => socket.close();
  }, []);

  const sectors = useMemo(() => dashboard?.sectors.filter((item) => sector === "all" || item.name === sector) ?? [], [dashboard, sector]);
  const weeklySectors = useMemo(() => weekly?.sectors.filter((item) => sector === "all" || item.name === sector) ?? [], [weekly, sector]);
  const monthlySectors = useMemo(() => monthly?.sectors.filter((item) => sector === "all" || item.name === sector) ?? [], [monthly, sector]);
  const radarCandidates = useMemo(() => {
    const candidates = [...(growthRadar?.candidates ?? [])].filter((item) =>
      (sector === "all" || item.sector === sector)
      && (radarStage === "all" || item.state === radarStage)
      && (radarTrack === "all" || item.track_eligibility[radarTrack as keyof GrowthCandidate["track_eligibility"]])
    );
    if (radarAlgorithm !== "strength_score") {
      candidates.sort((left, right) => (right.algorithm_scores[radarAlgorithm] ?? 0) - (left.algorithm_scores[radarAlgorithm] ?? 0));
    }
    return candidates;
  }, [growthRadar, sector, radarStage, radarTrack, radarAlgorithm]);
  const futureStocks = useMemo(() => (futureForecast?.predictions ?? []).filter((s) =>
    (futureConviction === "all" || s.conviction === futureConviction) &&
    (futureSector === "all" || s.sector === futureSector)
  ), [futureForecast, futureConviction, futureSector]);
  const futureSectors = useMemo(() => Array.from(new Set((futureForecast?.predictions ?? []).map((s) => s.sector))).sort(), [futureForecast]);
  const avgTargetReturn = useMemo(() => {
    const returns = futureStocks.map((s) => s.implied_cagr_pct);
    return returns.length ? returns.reduce((a, b) => a + b, 0) / returns.length : 0;
  }, [futureStocks]);
  const switchView = (next: "signals" | "weekly" | "monthly" | "radar" | "analysis" | "future") => { setView(next); setSector("all"); setError(""); setOperationStatus(""); };

  if (!authReady) {
    return <main className="auth-loading">Loading…</main>;
  }

  return (
    <main>
      <header className="topbar">
        <span className="logo">N</span><strong>NSE Operations Desk</strong>
        <nav className="menu" aria-label="Dashboard">
          {allowedMenus.map((item) => (
            <button key={item.key} className={view === item.key ? "active" : ""} onClick={() => switchView(item.key)}>{item.label}</button>
          ))}
          {(currentUser?.role === "admin" || currentUser?.permissions.includes("five_percent_strategy")) && (
            <Link href="/ai-five-percent-strategy" className="menu-link">AI 5% Growth Strategy</Link>
          )}
          <Link href="/bookmarks" className="menu-link">My Bookmarks</Link>
        </nav>
        <span className={`status ${stream}`}>Alerts {stream}</span>
        {currentUser?.role === "admin" && <a className="admin-link" href="/admin">Admin</a>}
        <button className="logout-button" onClick={logout}>Logout ({currentUser?.username})</button>
      </header>
      {operationStatus && <div className="notice" role="status">{operationStatus}</div>}

      {view === "signals" ? <>
        <section className="hero"><div><p className="eyebrow">MARKET OPERATIONS</p><h1>Signals and alerts,<br /><em>one live view.</em></h1></div><button onClick={() => void loadSignals(true)} disabled={loading}>Refresh scan</button></section>
        {error && <div className="error" role="alert">{error}</div>}
        <section className="stats"><div><span>Market regime</span><b>{dashboard?.regime?.state ?? "UNAVAILABLE"}</b></div><div><span>Paper equity</span><b>{portfolio ? `₹${portfolio.summary.equity.toLocaleString("en-IN")}` : "—"}</b></div><div><span>Drawdown</span><b>{portfolio ? `${portfolio.summary.drawdown_pct}%` : "—"}</b></div><div><span>Open positions</span><b>{portfolio?.summary.open_positions ?? 0}</b></div></section>
        <div className="workspace"><section><div className="controls"><div><button className={side === "buys" ? "active" : ""} onClick={() => setSide("buys")}>Buy signals</button><button className={side === "sells" ? "active" : ""} onClick={() => setSide("sells")}>Sell signals</button></div><select aria-label="Sector" value={sector} onChange={(event) => setSector(event.target.value)}><option value="all">All sectors</option>{dashboard?.sectors.map((item) => <option key={item.name}>{item.name}</option>)}</select></div>
          {loading && <div className="empty">Scanning market data…</div>}
          <div className="sector-grid">{sectors.map((item) => <section className="card" key={item.name}><h2>{item.name}<small>{item.scanned} screened</small></h2>{item[side].length ? item[side].map((stock) => <div className="stock" key={stock.symbol}><div><b>{stock.name}</b><small>{stock.symbol}</small></div><span>₹{stock.price.toLocaleString("en-IN")}</span><span className={stock.change_pct >= 0 ? "positive" : "negative"}>{stock.change_pct > 0 ? "+" : ""}{stock.change_pct}%</span><mark className={side}>{stock.score > 0 ? "+" : ""}{stock.score}</mark><button className={`follow-button${bookmarkedSymbols.has(stock.symbol) ? " following" : ""}`} onClick={() => void toggleBookmark(stock.symbol)}>{bookmarkedSymbols.has(stock.symbol) ? "★ Following" : "☆ Follow"}</button></div>) : <div className="empty">No qualifying signals</div>}</section>)}</div></section>
          <aside className="alerts"><h2>Alert center <small>Latest 50</small></h2>{alerts.length ? alerts.map((alert, index) => <article key={`${alert.symbol}-${alert.created_at}-${index}`}><span className={`alert-side ${alert.signal.toLowerCase()}`}>{alert.signal}</span><div><b>{alert.symbol}</b><small>{alert.sector ?? "Unclassified"} · {new Date(alert.created_at).toLocaleTimeString("en-IN")}</small></div><strong>{alert.score > 0 ? "+" : ""}{alert.score}</strong></article>) : <div className="empty">No persisted alerts yet</div>}</aside></div>
      </> : view === "weekly" ? <>
        <section className="hero weekly-hero"><div><p className="eyebrow">COMPLETED WEEKLY CANDLE SCREEN</p><h1>Weekly buy and sell<br /><em>crossover indicators.</em></h1><p className="hero-copy">Exact MACD, EMA and ADX conditions, ranked up to five per Nifty 500 industry.</p></div><button onClick={() => void loadWeekly(true)} disabled={loading}>{loading ? "Generating…" : "Generate indicators"}</button></section>
        {error && <div className="error" role="alert">{error}</div>}
        <section className="stats"><div><span>Market date</span><b>{weekly?.market_date ?? "—"}</b></div><div><span>Buy indicators</span><b>{weekly?.buy_count ?? 0}</b></div><div><span>Sell indicators</span><b>{weekly?.sell_count ?? 0}</b></div><div><span>Universe</span><b>{weekly?.universe_size ?? 0}</b></div></section>
        <section className="weekly-workspace"><div className="controls"><div><button className={side === "buys" ? "active" : ""} onClick={() => setSide("buys")}>Buy indicators</button><button className={side === "sells" ? "active" : ""} onClick={() => setSide("sells")}>Sell indicators</button></div><select aria-label="Sector" value={sector} onChange={(event) => setSector(event.target.value)}><option value="all">All sectors</option>{weekly?.sectors.map((item) => <option key={item.name}>{item.name}</option>)}</select></div>
          {loading && <div className="empty">Calculating weekly features and ranking eligible stocks…</div>}
          {!loading && weekly?.predictions_count === 0 && <div className="empty empty-panel">No persisted predictions are available. Generate predictions after market data is finalized.</div>}
          <div className="sector-grid weekly-grid">{weeklySectors.map((item) => <section className="card" key={item.name}><h2>{item.name}<small>Top {item[side]?.length ?? 0}</small></h2>{item[side]?.length ? item[side].map((pick) => <IndicatorPickCard pick={pick} side={side} key={pick.symbol} />) : <div className="empty">No exact {side === "buys" ? "buy" : "sell"} crossover</div>}</section>)}</div>
          <p className="disclaimer">{weekly?.disclaimer ?? "Model estimates are for research only and are not investment advice."}</p></section>
      </> : view === "monthly" ? <>
        <section className="hero monthly-hero"><div><p className="eyebrow">COMPLETED MONTHLY CANDLE SCREEN</p><h1>Monthly buy and sell<br /><em>crossover indicators.</em></h1><p className="hero-copy">The same strict Chartink conditions applied to completed monthly bars, with up to five results per industry.</p></div><button onClick={() => void loadMonthly(months, true)} disabled={loading}>{loading ? "Generating..." : "Generate indicators"}</button></section>
        {error && <div className="error" role="alert">{error}</div>}
        <section className="stats"><div><span>Market date</span><b>{monthly?.market_date ?? "—"}</b></div><div><span>Buy indicators</span><b>{monthly?.buy_count ?? 0}</b></div><div><span>Sell indicators</span><b>{monthly?.sell_count ?? 0}</b></div><div><span>Universe</span><b>{monthly?.universe_size ?? 0}</b></div></section>
        <section className="weekly-workspace monthly-workspace">
          <div className="monthly-controls">
            <div className="signal-toggle"><button className={side === "buys" ? "active" : ""} onClick={() => setSide("buys")}>Buy indicators</button><button className={side === "sells" ? "active" : ""} onClick={() => setSide("sells")}>Sell indicators</button></div>
            <label>Sector<select aria-label="Monthly sector" value={sector} onChange={(event) => setSector(event.target.value)}><option value="all">All sectors</option>{monthly?.sectors.map((item) => <option key={item.name}>{item.name}</option>)}</select></label>
          </div>
          {loading && <div className="empty">Calculating horizon-adjusted momentum, trend and risk scores...</div>}
          {!loading && monthly?.predictions_count === 0 && <div className="empty empty-panel">No persisted predictions exist for this interval. Generate them using finalized market data.</div>}
          <div className="sector-grid monthly-grid">{monthlySectors.map((item) => <section className="card" key={item.name}><h2>{item.name}<small>Top {item[side]?.length ?? 0}</small></h2>{item[side]?.length ? item[side].map((pick) => <IndicatorPickCard pick={pick} side={side} key={pick.symbol} />) : <div className="empty">No exact {side === "buys" ? "buy" : "sell"} crossover</div>}</section>)}</div>
          <section className="score-explanation"><div><p className="eyebrow">CHARTINK CROSSOVER SYSTEM</p><h2>Exact completed-candle indicators.</h2></div><p>BUY requires MACD and signal above zero, a fresh bullish cross with rising histogram, EMA 21 &gt; 50 &gt; 200, ADX &gt; 25 and ADX &gt; +DI &gt; -DI. SELL is the exact bearish inverse. Only completed monthly candles are evaluated.</p></section>
          <p className="disclaimer">{monthly?.disclaimer ?? "Model estimates are for research only and are not investment advice."}</p>
        </section>
      </> : view === "radar" ? <>
        <section className="hero radar-hero"><div><p className="eyebrow">6–12 MONTH EARLY DISCOVERY</p><h1>Find operating strength<br /><em>before the rerating.</em></h1><p className="hero-copy">Independent earnings, order-book, turnaround, accumulation, valuation, ownership and catalyst models with point-in-time evidence.</p></div><button onClick={() => void loadGrowthRadar(true)} disabled={loading}>{loading ? "Generating..." : "Generate radar"}</button></section>
        {error && <div className="error" role="alert">{error}</div>}
        <section className="stats"><div><span>Market date</span><b>{growthRadar?.market_date ?? "—"}</b></div><div><span>Eligible stocks</span><b>{growthRadar?.eligible_stocks ?? 0}</b></div><div><span>Displayed</span><b>{radarCandidates.length}</b></div><div><span>Universe</span><b>{growthRadar?.universe_size ?? 0}</b></div></section>
        <section className="radar-workspace">
          <div className="radar-controls">
            <label>Rank by<select value={radarAlgorithm} onChange={(event) => setRadarAlgorithm(event.target.value)}><option value="strength_score">Combined strength</option><option value="earnings_inflection">Earnings inflection</option><option value="order_book_capex">Order book & capex</option><option value="turnaround_deleveraging">Turnaround</option><option value="price_volume_accumulation">Accumulation</option><option value="valuation">Valuation</option><option value="ownership">Ownership</option><option value="catalyst">Catalyst</option></select></label>
            <label>Sector<select value={sector} onChange={(event) => setSector(event.target.value)}><option value="all">All sectors</option>{Array.from(new Set(growthRadar?.candidates.map((item) => item.sector) ?? [])).sort().map((name) => <option key={name}>{name}</option>)}</select></label>
            <label>Stage<select value={radarStage} onChange={(event) => setRadarStage(event.target.value)}><option value="all">All stages</option><option value="EARLY_WATCH">Early watch</option><option value="BUILDING_STRENGTH">Building strength</option><option value="QUALIFIED">Qualified</option><option value="BREAKOUT_CONFIRMED">Breakout confirmed</option></select></label>
            <label>Track<select value={radarTrack} onChange={(event) => setRadarTrack(event.target.value)}><option value="all">Both tracks</option><option value="compounder_12m">12M compounder</option><option value="multibagger_24m">24M multibagger</option></select></label>
          </div>
          {loading && <div className="empty empty-panel">Calculating point-in-time factor strength and scenario valuations...</div>}
          {!loading && radarCandidates.length === 0 && <div className="empty empty-panel">No persisted candidates match these filters. Ingest growth factors and run the radar worker.</div>}
          <div className="radar-list">{radarCandidates.map((candidate) => <GrowthRadarCard candidate={candidate} expanded={expandedGrowth === candidate.symbol} onToggle={() => setExpandedGrowth(expandedGrowth === candidate.symbol ? null : candidate.symbol)} key={candidate.symbol} />)}</div>
          <p className="disclaimer">{growthRadar?.disclaimer ?? "Scenario prices are research estimates, not assured targets or investment advice."}</p>
        </section>
      </> : view === "future" ? <>
        <section className="hero future-hero"><div><p className="eyebrow">ML FORWARD RETURN SCREEN</p><h1>Future Stocks,<br /><em>ranked by model edge.</em></h1><p className="hero-copy">Daily NSE 500 one-year return forecasts with LightGBM inference and SHAP drivers for each ranked stock.</p></div><div className="future-hero-right"><div className="future-hero-badge"><span>{futureForecast?.model_version ?? "v1"}</span><small>Model version</small></div><button className="future-refresh-btn" onClick={() => void loadFutureForecast(true)} disabled={futureLoading}>{futureLoading ? "Queueing..." : "Run inference"}</button></div></section>
        {pricesError && <div className="error" role="alert">{pricesError}</div>}
        <section className="stats"><div><span>Stocks displayed</span><b>{futureStocks.length} / {futureForecast?.predictions_count ?? futureForecast?.predictions.length ?? 0}</b></div><div><span>Avg forecast return</span><b className="positive">{formatPercent(avgTargetReturn, 0)}</b></div><div><span>High conviction</span><b>{futureStocks.filter((s) => s.conviction === "HIGH").length}</b></div><div><span>Generated</span><b>{futureForecast?.generated_at ? new Date(futureForecast.generated_at).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" }) : "-"}</b></div></section>
        <section className="future-workspace">
          <section className="future-price-checker">
            <div>
              <p className="eyebrow">YAHOO CLOSE CHECK</p>
              <h2>Check today close</h2>
            </div>
            <div className="future-price-form">
              <label htmlFor="future-symbol">Symbol</label>
              <input id="future-symbol" value={futureLookupSymbol} placeholder="RELIANCE.NS" onChange={(event) => setFutureLookupSymbol(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void lookupFuturePrice(); }} />
              <button onClick={() => void lookupFuturePrice()} disabled={pricesLoading}>{pricesLoading ? "Checking..." : "Check close"}</button>
            </div>
            {futureLookupPrice && <div className="future-price-result">
              <div><small>{priceBasisLabel(futureLookupPrice)}</small><b>Rs {futureLookupPrice.close.toLocaleString("en-IN", { maximumFractionDigits: 2 })}</b><span>{futureLookupPrice.symbol} | {futureLookupPrice.as_of || "latest"} IST</span></div>
              <div><small>Change</small><b className={futureLookupPrice.change_pct >= 0 ? "positive" : "negative"}>{formatPercent(futureLookupPrice.change_pct)}</b><span>Vs previous close Rs {formatNumber(futureLookupPrice.previous_close)}</span></div>
              <div><small>Day range</small><b>{formatNumber(futureLookupPrice.day_low)} - {formatNumber(futureLookupPrice.day_high)}</b><span>{futureLookupPrice.name}</span></div>
            </div>}
          </section>
          <div className="future-controls">
            <label>Conviction<select value={futureConviction} onChange={(e) => setFutureConviction(e.target.value)}><option value="all">All conviction</option><option value="HIGH">High</option><option value="MEDIUM">Medium</option><option value="SPECULATIVE">Speculative</option></select></label>
            <label>Sector<select value={futureSector} onChange={(e) => setFutureSector(e.target.value)}><option value="all">All sectors</option>{futureSectors.map((s) => <option key={s}>{s}</option>)}</select></label>
          </div>
          {futureLoading && <div className="empty empty-panel">Loading ML forecasts...</div>}
          {!futureLoading && futureStocks.length === 0 && <div className="empty empty-panel">No ML forecasts match these filters. Run inference after Redis, Postgres and the worker are configured.</div>}
          <div className="future-list">{futureStocks.map((stock) => <MLFutureCard key={stock.symbol} stock={stock} expanded={expandedFuture === stock.symbol} onToggle={() => setExpandedFuture(expandedFuture === stock.symbol ? null : stock.symbol)} />)}</div>
          <p className="disclaimer">{futureForecast?.disclaimer ?? "Model estimates are research signals, not guaranteed returns or investment advice."}</p>
        </section>
      </> : <>
        <section className="hero analysis-hero"><div><p className="eyebrow">DEEP DIVE STOCK ANALYSIS</p><h1>One stock,<br /><em>show the work.</em></h1><p className="hero-copy">Unified signal, factor-by-factor contribution, condition flags and historical percentile return projection.</p></div></section>
        <section className="analysis-search">
          <label htmlFor="analysis-symbol">NSE or BSE symbol</label>
          <div><input id="analysis-symbol" value={analysisSymbol} placeholder="RELIANCE.NS or 500325.BO" onChange={(event) => setAnalysisSymbol(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void loadAnalysis(); }} /><button onClick={() => void loadAnalysis()} disabled={loading}>{loading ? "Analyzing..." : "Analyze stock"}</button></div>
        </section>
        {error && <div className="error" role="alert">{error}</div>}
        {!analysis && !loading && <section className="analysis-empty"><b>Enter a stock symbol to begin.</b><span>Symbols without a suffix default to NSE. Use .BO for BSE listings.</span></section>}
        {analysis && <section className="analysis-workspace">
          <div className="analysis-head">
            <div><p className="eyebrow">{analysis.sector} · {analysis.source}</p><h2>{analysis.name}</h2><span>{analysis.symbol} · {analysis.as_of ?? "Latest available session"}</span></div>
            <div className={`analysis-signal ${analysis.overall_signal.toLowerCase()}`}><small>Overall signal</small><strong>{analysis.overall_signal.replaceAll("_", " ")}</strong><span>Score {analysis.overall_score}/100 | {analysis.confidence_interval} confidence</span></div>
          </div>
          <div className="analysis-stats">
            <div><small>Price</small><b>{analysis.price ? `₹${analysis.price.toLocaleString("en-IN")}` : "—"}</b></div>
            <div><small>Overall score</small><b>{analysis.overall_score}</b></div>
            <div><small>Technical</small><b>{analysis.factor_breakdown.technical.score}</b></div>
            <div><small>Fundamental</small><b>{analysis.factor_breakdown.fundamental.score}</b></div>
            <div><small>15D median</small><b>{formatPercent(analysis.projected_returns.horizon_15d.median)}</b></div>
            <div><small>15D hit rate</small><b>{formatPercent(analysis.projected_returns.horizon_15d.hit_rate, 0)}</b></div>
          </div>
          <div className="analysis-grid">
            <article className="analysis-card"><h3>Technical factors</h3><dl><div><dt>Trend</dt><dd>{analysis.factor_breakdown.technical.trend.display}</dd></div><div><dt>Momentum</dt><dd>{analysis.factor_breakdown.technical.momentum.display}</dd></div><div><dt>Volatility</dt><dd>{analysis.factor_breakdown.technical.volatility.display}</dd></div><div><dt>SMA20</dt><dd>{analysis.factor_breakdown.technical.price_vs_sma20.display}</dd></div></dl><div className="flag-list">{analysis.factor_breakdown.technical.condition_flags.map((flag) => <span key={flag}>{flag.replaceAll("_", " ")}</span>)}</div></article>
            <article className="analysis-card"><h3>Fundamental factors</h3><dl><div><dt>Coverage</dt><dd>{analysis.factor_breakdown.fundamental.coverage}</dd></div><div><dt>Valuation</dt><dd>{analysis.factor_breakdown.fundamental.valuation.display}</dd></div><div><dt>Quality</dt><dd>{analysis.factor_breakdown.fundamental.quality.display}</dd></div><div><dt>Growth</dt><dd>{analysis.factor_breakdown.fundamental.growth.display}</dd></div></dl></article>
            <article className="analysis-card"><h3>Smart money / options</h3><dl><div><dt>Coverage</dt><dd>{analysis.factor_breakdown.smart_money_options.coverage}</dd></div><div><dt>PCR</dt><dd>{analysis.factor_breakdown.smart_money_options.display}</dd></div><div><dt>OI change</dt><dd>{formatPercent(analysis.factor_breakdown.smart_money_options.oi_change)}</dd></div><div><dt>IV skew</dt><dd>{formatNumber(analysis.factor_breakdown.smart_money_options.iv_skew)}</dd></div><div><dt>GEX</dt><dd>{formatNumber(analysis.factor_breakdown.smart_money_options.gex, 0)}</dd></div></dl></article>
            <article className="analysis-card"><h3>News, sentiment and legal</h3><dl><div><dt>Coverage</dt><dd>{analysis.factor_breakdown.news_sentiment_legal.coverage}</dd></div><div><dt>FinBERT score</dt><dd>{formatNumber(analysis.factor_breakdown.news_sentiment_legal.sentiment_score)}</dd></div><div><dt>Legal risk</dt><dd>{analysis.factor_breakdown.news_sentiment_legal.legal_risk ?? "Unknown"}</dd></div><div><dt>Display</dt><dd>{analysis.factor_breakdown.news_sentiment_legal.display}</dd></div></dl></article>
            <article className="analysis-card factor-card"><h3>Score contribution</h3>{Object.entries(analysis.score_contributions).map(([name, points]) => <div className="factor-row" key={name}><span>{name.replaceAll("_", " ")}</span><i><em style={{ width: `${Math.max(0, Math.min(100, points * 3))}%` }} /></i><b>{points}</b><small>pts</small></div>)}</article>
            <article className="analysis-card projection-card"><h3>What-if return projection</h3><div className="projection-horizons"><ProjectionTile label="5D" value={analysis.projected_returns.horizon_5d} /><ProjectionTile label="15D" value={analysis.projected_returns.horizon_15d} /><ProjectionTile label="30D" value={analysis.projected_returns.horizon_30d} /></div><p>Matched {analysis.projected_returns.sample_size} historical sessions within +/-5 score points.</p>{analysis.data_warnings.length > 0 && <div className="analysis-warnings"><b>Data warnings</b>{analysis.data_warnings.map((warning) => <span key={warning}>{warning}</span>)}</div>}</article>
          </div>
          <p className="disclaimer">{analysis.disclaimer}</p>
        </section>}
      </>}
    </main>
  );
}

function GrowthRadarCard({ candidate, expanded, onToggle }: { candidate: GrowthCandidate; expanded: boolean; onToggle: () => void }) {
  const algorithms = Object.entries(candidate.algorithm_scores).sort((left, right) => right[1] - left[1]);
  return <article className={`growth-card ${candidate.state.toLowerCase()}`}>
    <button className="growth-summary" onClick={onToggle} aria-expanded={expanded}>
      <span className="prediction-rank">{candidate.rank}</span>
      <span className="growth-name"><b>{candidate.name}</b><small>{candidate.symbol} · {candidate.sector} · signal {candidate.signal_date}</small></span>
      <span><small>Signal price</small><b>₹{candidate.signal_price.toLocaleString("en-IN")}</b></span>
      <span><small>Current</small><b>₹{candidate.current_price.toLocaleString("en-IN")}</b></span>
      <span className={candidate.return_since_signal_pct >= 0 ? "positive" : "negative"}><small>Since signal</small><b>{candidate.return_since_signal_pct > 0 ? "+" : ""}{candidate.return_since_signal_pct}%</b></span>
      <span><small>Strength</small><b>{candidate.strength_score}</b></span>
      <mark className="growth-stage">{candidate.state.replaceAll("_", " ")}</mark>
    </button>
    {expanded && <div className="growth-detail">
      <div className="growth-factor-panel"><h3>Stock strength</h3>{algorithms.map(([name, value]) => <div className="factor-row growth-factor" key={name}><span>{name.replaceAll("_", " ")}</span><i><em style={{ width: `${Math.max(0, Math.min(100, value))}%` }} /></i><b>{Math.round(value)}</b></div>)}<p>Confidence {candidate.confidence_pct}% · Data {candidate.data_freshness.toLowerCase()} · Penalty {candidate.penalty}</p>{candidate.risk_flags.length > 0 && <div className="analysis-warnings"><b>Risk flags</b><span>{candidate.risk_flags.join(" · ").replaceAll("_", " ")}</span></div>}</div>
      <div className="growth-projection-panel"><h3>FY2027–FY2035 scenario range</h3>{candidate.projections.available ? <><ProjectionFanChart years={candidate.projections.years} /><div className="scenario-cagr"><span>Bear CAGR <b>{candidate.projections.implied_cagr_pct?.bear}%</b></span><span>Base CAGR <b>{candidate.projections.implied_cagr_pct?.base}%</b></span><span>Bull CAGR <b>{candidate.projections.implied_cagr_pct?.bull}%</b></span></div></> : <div className="empty">{candidate.projections.reason}</div>}</div>
      {candidate.projections.available && <div className="projection-table-wrap"><table className="projection-table"><thead><tr><th>FY</th><th>Bear price</th><th>Base price</th><th>Base YoY</th><th>Bull price</th><th>Base margin</th><th>Base EPS</th></tr></thead><tbody>{candidate.projections.years.map((year) => <tr key={year.fiscal_year}><td>{year.fiscal_year}</td><td>₹{year.bear.price.toLocaleString("en-IN")}</td><td><b>₹{year.base.price.toLocaleString("en-IN")}</b></td><td className={year.base.year_growth_pct >= 0 ? "positive" : "negative"}>{year.base.year_growth_pct}%</td><td>₹{year.bull.price.toLocaleString("en-IN")}</td><td>{year.base.ebitda_margin_pct}%</td><td>{year.base.eps ?? "—"}</td></tr>)}</tbody></table></div>}
      <div className="growth-evidence"><h3>Point-in-time evidence</h3>{candidate.evidence.length ? candidate.evidence.map((item, index) => item.source_url ? <a href={item.source_url} target="_blank" rel="noreferrer" key={`${item.source_url}-${index}`}>{item.title ?? item.source ?? "Source filing"}<small>{item.published_at ?? ""}</small></a> : <span key={index}>{item.title ?? item.source ?? "Stored evidence"}</span>) : <span>No source links were attached to this factor snapshot.</span>}</div>
    </div>}
  </article>;
}

function ProjectionFanChart({ years }: { years: ProjectionYear[] }) {
  const width = 720, height = 210, pad = 28;
  const values = years.flatMap((year) => [year.bear.price, year.base.price, year.bull.price]);
  const maximum = Math.max(...values, 1);
  const point = (price: number, index: number) => {
    const x = pad + index * ((width - pad * 2) / Math.max(1, years.length - 1));
    const y = height - pad - price / maximum * (height - pad * 2);
    return `${x},${y}`;
  };
  const line = (scenario: "bear" | "base" | "bull") => years.map((year, index) => point(year[scenario].price, index)).join(" ");
  const fan = [
    ...years.map((year, index) => point(year.bull.price, index)),
    ...[...years].reverse().map((year, reverseIndex) => point(year.bear.price, years.length - 1 - reverseIndex)),
  ].join(" ");
  return <svg className="projection-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Bear, base and bull price scenarios through fiscal year 2035">
    <polygon points={fan} className="scenario-fan" />
    <polyline points={line("bear")} className="scenario-line bear" />
    <polyline points={line("base")} className="scenario-line base" />
    <polyline points={line("bull")} className="scenario-line bull" />
    {years.map((year, index) => <text x={pad + index * ((width - pad * 2) / Math.max(1, years.length - 1))} y={height - 6} textAnchor="middle" key={year.fiscal_year}>FY{String(year.fiscal_year).slice(-2)}</text>)}
  </svg>;
}

function ProjectionTile({ label, value }: { label: string; value: DeepDiveReturn }) {
  return <div className="projection-tile">
    <span>{label}</span>
    <b className={(value.median ?? 0) >= 0 ? "positive" : "negative"}>{formatPercent(value.median)}</b>
    <small>Range {formatPercent(value.lower)} to {formatPercent(value.upper)}</small>
    <small>Hit {formatPercent(value.hit_rate, 0)}</small>
  </div>;
}

function IndicatorPickCard({ pick, side }: { pick: WeeklyPick | MonthlyPick; side: "buys" | "sells" }) {
  const indicator = pick.indicator;
  const rank = side === "buys" ? pick.buy_rank : pick.sell_rank;
  const features = indicator.features ?? {};
  return <article className={`indicator-pick ${side}`}>
    <span className="prediction-rank">{rank ?? "—"}</span>
    <div className="prediction-name"><b>{pick.name}</b><small>{pick.symbol} · ₹{pick.price.toLocaleString("en-IN")} · {indicator.as_of}</small></div>
    <div className="prediction-metric"><small>MACD / signal</small><strong>{features.macd} / {features.macd_signal}</strong></div>
    <div className="prediction-metric"><small>ADX / DI</small><strong>{features.adx_14} / {side === "buys" ? features.positive_di_14 : features.negative_di_14}</strong></div>
    <div className={`indicator-badge ${side}`}>{indicator.signal} {Math.round(indicator.strength_score)}</div>
  </article>;
}

function ScoreBar({ label, value, maximum }: { label: string; value: number; maximum: number }) {
  const width = Math.max(0, Math.min(100, value / maximum * 100));
  return <div className="score-bar"><span>{label}</span><i><em style={{ width: `${width}%` }} /></i><b>{value}/{maximum}</b></div>;
}

function MonthlyPickCard({ pick }: { pick: MonthlyPick }) {
  const breakdown = pick.score_breakdown ?? {};
  const hasConservativeScore = breakdown.relative_strength !== undefined;
  const state = pick.state ?? "LEGACY";
  const stop = pick.entry?.proposed_stop;
  const quantity = pick.entry?.quantity;
  const reasons = pick.reasons ?? [];
  const rejectionReasons = pick.rejection_reasons ?? [];

  return <article className="monthly-pick">
    <div className="monthly-summary">
      <span className="prediction-rank">{pick.sector_rank}</span>
      <div className="prediction-name"><b>{pick.name} {pick.nifty_50_member ? "· N50" : ""}</b><small>{pick.symbol} · ₹{pick.price.toLocaleString("en-IN")}</small></div>
      <div className="total-score"><small>Score</small><strong>{pick.score}</strong><span>/100</span></div>
      <div className="prediction-metric"><small>{state}</small><strong>{stop !== undefined ? `Stop ₹${stop}` : `${pick.horizon_months}M estimate`}</strong></div>
      <div className={`risk ${state === "BUY_READY" ? "ready" : "normal"}`}>{quantity !== undefined ? `${quantity} shares` : `Risk ${Math.round(pick.risk_score)}`}</div>
    </div>
    <div className="score-bars">
      {hasConservativeScore ? <>
        <ScoreBar label="Relative strength" value={breakdown.relative_strength ?? 0} maximum={30} />
        <ScoreBar label="12-1 momentum" value={breakdown.momentum_12_1 ?? 0} maximum={25} />
        <ScoreBar label="6M momentum" value={breakdown.momentum_6m ?? 0} maximum={20} />
        <ScoreBar label="Trend" value={breakdown.trend_strength ?? 0} maximum={15} />
        <ScoreBar label="Quality" value={breakdown.liquidity_volatility ?? 0} maximum={10} />
      </> : <>
        <ScoreBar label="Trend" value={breakdown.trend ?? 0} maximum={30} />
        <ScoreBar label="Momentum" value={breakdown.momentum ?? 0} maximum={30} />
        <ScoreBar label="Volume" value={breakdown.volume ?? 0} maximum={10} />
        <ScoreBar label="RSI" value={breakdown.rsi_quality ?? 0} maximum={10} />
        <ScoreBar label="Risk control" value={breakdown.risk_control ?? 0} maximum={20} />
      </>}
    </div>
    <p className="pick-reasons">{state === "WATCHLIST" && rejectionReasons.length ? `Waiting: ${rejectionReasons.slice(0, 3).join(" · ")}` : reasons.join(" · ") || "Legacy prediction—generate again for entry readiness."}</p>
  </article>;
}
