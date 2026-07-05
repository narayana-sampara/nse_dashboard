"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { apiFetch, fetchCurrentUser, getToken, type CurrentUser } from "../../lib/auth";

type Candidate = {
  symbol: string;
  company_name: string | null;
  sector: string | null;
  close_price: number;
  entry_price: number;
  target_price: number;
  stop_loss_price: number;
  probability_score: number;
  ai_score: number;
  rank: number;
  expected_return_pct: number;
  risk_reward_ratio: number;
  avg_volume: number;
  avg_turnover: number;
  volatility: number;
  rsi: number;
  momentum_5d: number;
  momentum_20d: number;
  volume_ratio: number;
  trend_score: number;
  relative_strength_score: number;
  breakout_score: number;
  risk_score: number;
  reasons: string[];
};

type ScanResult = {
  run_id: string | null;
  created_at: string | null;
  market_date: string | null;
  candidates_count: number;
  candidates: Candidate[];
  disclaimer?: string;
};

type BacktestTrade = {
  symbol: string;
  entry_date: string;
  exit_date: string | null;
  result: string;
  return_pct: number;
  capital_after: number;
  exit_reason: string;
};

type BacktestResult = {
  backtest_id: string;
  final_capital: number;
  total_return_pct: number;
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  win_rate: number;
  average_win_pct: number;
  average_loss_pct: number;
  max_drawdown_pct: number;
  profit_factor: number | null;
  longest_win_streak: number;
  longest_loss_streak: number;
  equity_curve: { date: string; capital: number }[];
  trades: BacktestTrade[];
};

type ProjectionScenario = { win_rate_pct: number; final_capital: number | null; total_return_pct: number | null };
type ProjectionResult = { scenarios: Record<string, ProjectionScenario>; disclaimer: string };

type PaperTrade = {
  id: number;
  symbol: string;
  entry_date: string;
  entry_price: number;
  current_price: number | null;
  target_price: number;
  stop_loss_price: number;
  status: string;
  return_pct: number | null;
  exit_reason: string | null;
};

const DISCLAIMER =
  "This module generates research-based trading signals using historical data, technical factors, and probability scoring. It does not guarantee 5% returns. Trading involves risk, including loss of capital. Backtested results may not match live performance due to slippage, costs, liquidity, and market conditions.";

export default function AiFivePercentStrategyPage() {
  const router = useRouter();
  const [ready, setReady] = useState(false);
  const [currentUser, setCurrentUser] = useState<CurrentUser | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [scan, setScan] = useState<ScanResult | null>(null);
  const [backtest, setBacktest] = useState<BacktestResult | null>(null);
  const [projection, setProjection] = useState<ProjectionResult | null>(null);
  const [paperTrades, setPaperTrades] = useState<PaperTrade[]>([]);
  const [selected, setSelected] = useState<Candidate | null>(null);
  const [stream, setStream] = useState<"connecting" | "live" | "offline">("connecting");

  const [initialCapital, setInitialCapital] = useState(10_000);
  const [targetPct, setTargetPct] = useState(5);
  const [stopLossPct, setStopLossPct] = useState(2);
  const [holdingDays, setHoldingDays] = useState(5);
  const [probabilityThreshold, setProbabilityThreshold] = useState(65);
  const [maxCandidates, setMaxCandidates] = useState(20);
  const [minAvgVolume, setMinAvgVolume] = useState(0);
  const [minAvgTurnover, setMinAvgTurnover] = useState(10_000_000);

  const [backtestStart, setBacktestStart] = useState("2023-01-01");
  const [backtestEnd, setBacktestEnd] = useState("2025-12-31");

  useEffect(() => {
    if (!getToken()) { router.replace("/login"); return; }
    void fetchCurrentUser().then((user) => {
      if (!user) { router.replace("/login"); return; }
      if (user.role !== "admin" && !user.permissions.includes("five_percent_strategy")) {
        router.replace("/");
        return;
      }
      setCurrentUser(user);
      setReady(true);
    });
  }, [router]);

  const loadLatest = useCallback(async () => {
    const response = await apiFetch("/api/v1/five-percent-strategy/latest", { cache: "no-store" });
    if (response.ok) setScan(await response.json());
  }, []);

  const loadPaperTrades = useCallback(async () => {
    const response = await apiFetch("/api/v1/five-percent-strategy/paper-trades", { cache: "no-store" });
    if (response.ok) setPaperTrades(await response.json());
  }, []);

  useEffect(() => {
    if (!ready) return;
    void loadLatest();
    void loadPaperTrades();
  }, [ready, loadLatest, loadPaperTrades]);

  useEffect(() => {
    if (!ready) return;
    const token = process.env.NEXT_PUBLIC_WEBSOCKET_TOKEN;
    const origin = process.env.NEXT_PUBLIC_API_ORIGIN;
    if (!token || !origin) return;
    const socket = new WebSocket(`${origin.replace(/^http/, "ws")}/api/v1/stream/signals?token=${encodeURIComponent(token)}`);
    socket.onopen = () => setStream("live");
    socket.onerror = () => setStream("offline");
    socket.onmessage = (event) => {
      const message = JSON.parse(event.data) as { type: string; data: unknown };
      if (message.type === "five_percent_strategy.scan_completed") setScan(message.data as ScanResult);
      if (message.type === "five_percent_strategy.backtest_completed") setBacktest(message.data as BacktestResult);
      if (
        message.type === "five_percent_strategy.paper_trade_target_hit" ||
        message.type === "five_percent_strategy.paper_trade_stop_hit"
      ) {
        void loadPaperTrades();
      }
    };
    return () => socket.close();
  }, [ready, loadPaperTrades]);

  const runScan = async () => {
    setLoading(true); setError("");
    try {
      const response = await apiFetch("/api/v1/five-percent-strategy/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          target_pct: targetPct,
          stop_loss_pct: stopLossPct,
          holding_days: holdingDays,
          probability_threshold: probabilityThreshold,
          max_candidates: maxCandidates,
          initial_capital: initialCapital,
          min_avg_volume: minAvgVolume,
          min_avg_turnover: minAvgTurnover,
        }),
      });
      if (!response.ok) throw new Error(`Scan failed (${response.status})`);
      setScan(await response.json());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to run scan");
    } finally {
      setLoading(false);
    }
  };

  const runBacktest = async () => {
    setLoading(true); setError("");
    try {
      const response = await apiFetch("/api/v1/five-percent-strategy/backtest", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          start_date: backtestStart,
          end_date: backtestEnd,
          initial_capital: initialCapital,
          target_pct: targetPct,
          stop_loss_pct: stopLossPct,
          holding_days: holdingDays,
          probability_threshold: probabilityThreshold,
        }),
      });
      if (!response.ok) throw new Error(`Backtest failed (${response.status})`);
      setBacktest(await response.json());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to run backtest");
    } finally {
      setLoading(false);
    }
  };

  const runProjection = async () => {
    const response = await apiFetch("/api/v1/five-percent-strategy/projection", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        initial_capital: initialCapital,
        target_pct: targetPct,
        stop_loss_pct: stopLossPct,
        number_of_trades: 200,
        expected_win_rate: probabilityThreshold,
        cost_per_trade_pct: 0.3,
      }),
    });
    if (response.ok) setProjection(await response.json());
  };

  const addToPaperTrade = async (candidate: Candidate) => {
    const response = await apiFetch("/api/v1/five-percent-strategy/paper-trades/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        symbol: candidate.symbol,
        entry_price: candidate.entry_price,
        target_price: candidate.target_price,
        stop_loss_price: candidate.stop_loss_price,
        capital_before: initialCapital,
      }),
    });
    if (response.ok) void loadPaperTrades();
  };

  const highestAiScore = useMemo(
    () => (scan?.candidates.length ? Math.max(...scan.candidates.map((c) => c.ai_score)) : 0),
    [scan]
  );
  const averageProbability = useMemo(() => {
    if (!scan?.candidates.length) return 0;
    return Math.round((scan.candidates.reduce((sum, c) => sum + c.probability_score, 0) / scan.candidates.length) * 10) / 10;
  }, [scan]);
  const activePaperTrades = useMemo(() => paperTrades.filter((t) => t.status === "open").length, [paperTrades]);

  if (!ready) return <main className="auth-loading">Loading…</main>;

  return (
    <main className="five-percent-page">
      <header className="topbar">
        <span className="logo">N</span>
        <strong>AI 5% Growth Strategy</strong>
        <a href="/">Back to dashboard</a>
        <span className={`status ${stream}`}>Live {stream}</span>
        <span>{currentUser?.username}</span>
      </header>

      <div className="five-percent-disclaimer" role="note">{DISCLAIMER}</div>
      {error && <div className="error" role="alert">{error}</div>}

      <section className="stats">
        <div><span>Latest scan</span><b>{scan?.created_at ? new Date(scan.created_at).toLocaleString("en-IN") : "—"}</b></div>
        <div><span>Candidates</span><b>{scan?.candidates_count ?? 0}</b></div>
        <div><span>Highest AI score</span><b>{highestAiScore}</b></div>
        <div><span>Average probability</span><b>{averageProbability}%</b></div>
        <div><span>Active paper trades</span><b>{activePaperTrades}</b></div>
        <div><span>Backtest win rate</span><b>{backtest ? `${backtest.win_rate}%` : "—"}</b></div>
        <div><span>Projected capital</span><b>{projection ? `₹${projection.scenarios.custom.final_capital?.toLocaleString("en-IN") ?? "—"}` : "—"}</b></div>
      </section>

      <section className="five-percent-controls card">
        <h2>Strategy controls</h2>
        <div className="controls-grid">
          <label>Initial capital<input type="number" value={initialCapital} onChange={(e) => setInitialCapital(Number(e.target.value))} /></label>
          <label>Target %<input type="number" value={targetPct} onChange={(e) => setTargetPct(Number(e.target.value))} /></label>
          <label>Stop-loss %<input type="number" value={stopLossPct} onChange={(e) => setStopLossPct(Number(e.target.value))} /></label>
          <label>Holding days<input type="number" value={holdingDays} onChange={(e) => setHoldingDays(Number(e.target.value))} /></label>
          <label>Probability threshold<input type="number" value={probabilityThreshold} onChange={(e) => setProbabilityThreshold(Number(e.target.value))} /></label>
          <label>Max candidates<input type="number" value={maxCandidates} onChange={(e) => setMaxCandidates(Number(e.target.value))} /></label>
          <label>Min avg volume<input type="number" value={minAvgVolume} onChange={(e) => setMinAvgVolume(Number(e.target.value))} /></label>
          <label>Min avg turnover<input type="number" value={minAvgTurnover} onChange={(e) => setMinAvgTurnover(Number(e.target.value))} /></label>
        </div>
        <div className="controls-actions">
          <button onClick={() => void runScan()} disabled={loading}>Run scan</button>
          <label>From<input type="date" value={backtestStart} onChange={(e) => setBacktestStart(e.target.value)} /></label>
          <label>To<input type="date" value={backtestEnd} onChange={(e) => setBacktestEnd(e.target.value)} /></label>
          <button onClick={() => void runBacktest()} disabled={loading}>Run backtest</button>
          <button onClick={() => void runProjection()}>Project compounding</button>
        </div>
      </section>

      <section className="card">
        <h2>Ranked candidates</h2>
        <table className="five-percent-table">
          <thead>
            <tr>
              <th>Rank</th><th>Symbol</th><th>Sector</th><th>Entry</th><th>Target</th><th>Stop-loss</th>
              <th>Probability</th><th>AI score</th><th>Risk/reward</th><th>Volume ratio</th><th>Momentum</th>
              <th>Trend</th><th>Reason</th><th>Action</th>
            </tr>
          </thead>
          <tbody>
            {(scan?.candidates ?? []).map((candidate) => (
              <tr key={candidate.symbol}>
                <td>{candidate.rank}</td>
                <td><b>{candidate.symbol}</b><br /><small>{candidate.company_name}</small></td>
                <td>{candidate.sector ?? "—"}</td>
                <td>₹{candidate.entry_price}</td>
                <td>₹{candidate.target_price}</td>
                <td>₹{candidate.stop_loss_price}</td>
                <td>{candidate.probability_score}%</td>
                <td>{candidate.ai_score}</td>
                <td>{candidate.risk_reward_ratio}</td>
                <td>{candidate.volume_ratio}x</td>
                <td>{candidate.momentum_5d}%</td>
                <td>{Math.round(candidate.trend_score)}</td>
                <td><small>{candidate.reasons[0] ?? "—"}</small></td>
                <td>
                  <button onClick={() => setSelected(candidate)}>Details</button>
                  <button onClick={() => void addToPaperTrade(candidate)}>Paper trade</button>
                </td>
              </tr>
            ))}
            {!scan?.candidates.length && (
              <tr><td colSpan={14} className="empty">No candidates yet — run a scan.</td></tr>
            )}
          </tbody>
        </table>
      </section>

      {selected && (
        <section className="card five-percent-drawer">
          <h2>{selected.symbol} details <button onClick={() => setSelected(null)}>Close</button></h2>
          <div className="component-scores">
            <div><span>Momentum</span><b>{Math.round(selected.momentum_5d)}</b></div>
            <div><span>Trend</span><b>{Math.round(selected.trend_score)}</b></div>
            <div><span>Volume</span><b>{selected.volume_ratio}x</b></div>
            <div><span>Breakout</span><b>{Math.round(selected.breakout_score)}</b></div>
            <div><span>Relative strength</span><b>{Math.round(selected.relative_strength_score)}</b></div>
            <div><span>Risk control</span><b>{Math.round(selected.risk_score)}</b></div>
          </div>
          <p>Entry ₹{selected.entry_price} · Target ₹{selected.target_price} · Stop-loss ₹{selected.stop_loss_price}</p>
          <ul>{selected.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
          <p className="five-percent-disclaimer">These are research signals, not guaranteed returns.</p>
        </section>
      )}

      <section className="card">
        <h2>Backtest summary</h2>
        {backtest ? (
          <>
            <div className="stats">
              <div><span>Final capital</span><b>₹{backtest.final_capital.toLocaleString("en-IN")}</b></div>
              <div><span>Total return</span><b>{backtest.total_return_pct}%</b></div>
              <div><span>Win rate</span><b>{backtest.win_rate}%</b></div>
              <div><span>Total trades</span><b>{backtest.total_trades}</b></div>
              <div><span>Max drawdown</span><b>{backtest.max_drawdown_pct}%</b></div>
              <div><span>Profit factor</span><b>{backtest.profit_factor ?? "∞"}</b></div>
            </div>
            <table className="five-percent-table">
              <thead><tr><th>Symbol</th><th>Entry</th><th>Exit</th><th>Result</th><th>Return %</th><th>Exit reason</th></tr></thead>
              <tbody>
                {backtest.trades.slice(0, 50).map((trade, index) => (
                  <tr key={`${trade.symbol}-${trade.entry_date}-${index}`}>
                    <td>{trade.symbol}</td><td>{trade.entry_date}</td><td>{trade.exit_date}</td>
                    <td className={trade.result === "win" ? "positive" : "negative"}>{trade.result}</td>
                    <td>{trade.return_pct}%</td><td>{trade.exit_reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        ) : <div className="empty">Run a backtest to see results.</div>}
      </section>

      <section className="card">
        <h2>Compounding projection</h2>
        {projection ? (
          <table className="five-percent-table">
            <thead><tr><th>Scenario</th><th>Win rate</th><th>Final capital</th><th>Total return</th></tr></thead>
            <tbody>
              {Object.entries(projection.scenarios).map(([name, scenario]) => (
                <tr key={name}>
                  <td>{name}</td><td>{scenario.win_rate_pct}%</td>
                  <td>{scenario.final_capital != null ? `₹${scenario.final_capital.toLocaleString("en-IN")}` : "—"}</td>
                  <td>{scenario.total_return_pct != null ? `${scenario.total_return_pct}%` : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : <div className="empty">Run a projection to see compounding scenarios.</div>}
        <p className="five-percent-disclaimer">Perfect compounding is theoretical. Real trading includes losses, brokerage, taxes, slippage, liquidity issues, and gap risk.</p>
      </section>

      <section className="card">
        <h2>Paper trading</h2>
        <table className="five-percent-table">
          <thead><tr><th>Symbol</th><th>Entry date</th><th>Entry</th><th>Current</th><th>Target</th><th>Stop-loss</th><th>Status</th><th>Return %</th><th>Exit reason</th></tr></thead>
          <tbody>
            {paperTrades.map((trade) => (
              <tr key={trade.id}>
                <td>{trade.symbol}</td><td>{trade.entry_date}</td><td>₹{trade.entry_price}</td>
                <td>{trade.current_price != null ? `₹${trade.current_price}` : "—"}</td>
                <td>₹{trade.target_price}</td><td>₹{trade.stop_loss_price}</td>
                <td>{trade.status}</td>
                <td>{trade.return_pct != null ? `${trade.return_pct}%` : "—"}</td>
                <td>{trade.exit_reason ?? "—"}</td>
              </tr>
            ))}
            {!paperTrades.length && <tr><td colSpan={9} className="empty">No paper trades yet.</td></tr>}
          </tbody>
        </table>
      </section>
    </main>
  );
}
