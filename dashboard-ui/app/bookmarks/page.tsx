"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { apiFetch, fetchCurrentUser, getToken, type CurrentUser } from "../../lib/auth";

type Bookmark = {
  id: number;
  symbol: string;
  bookmark_price: number;
  created_at: string;
  current_price: number | null;
  growth_pct: number | null;
};

export default function BookmarksPage() {
  const router = useRouter();
  const [ready, setReady] = useState(false);
  const [currentUser, setCurrentUser] = useState<CurrentUser | null>(null);
  const [bookmarks, setBookmarks] = useState<Bookmark[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!getToken()) { router.replace("/login"); return; }
    void fetchCurrentUser().then((user) => {
      if (!user) { router.replace("/login"); return; }
      setCurrentUser(user);
      setReady(true);
    });
  }, [router]);

  const loadBookmarks = useCallback(async () => {
    setLoading(true); setError("");
    try {
      const response = await apiFetch("/api/v1/bookmarks", { cache: "no-store" });
      if (!response.ok) throw new Error(`Bookmarks API returned ${response.status}`);
      setBookmarks(await response.json());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to load bookmarks");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { if (ready) void loadBookmarks(); }, [ready, loadBookmarks]);

  const unfollow = async (symbol: string) => {
    const response = await apiFetch(`/api/v1/bookmarks/${symbol}`, { method: "DELETE" });
    if (response.ok) setBookmarks((prev) => prev.filter((item) => item.symbol !== symbol));
  };

  if (!ready) return <main className="auth-loading">Loading…</main>;

  return (
    <main className="bookmarks-page">
      <header className="topbar">
        <span className="logo">N</span>
        <strong>My Bookmarks</strong>
        <a href="/">Back to dashboard</a>
        <span>{currentUser?.username}</span>
      </header>

      {error && <div className="error" role="alert">{error}</div>}
      {loading && <div className="empty">Loading bookmarks…</div>}
      {!loading && bookmarks.length === 0 && (
        <div className="empty empty-panel">
          No bookmarks yet. Follow a stock from the Signals screen to track it here.
        </div>
      )}

      {!loading && bookmarks.length > 0 && (
        <section className="card">
          <div className="bookmark-row bookmark-head">
            <span>Symbol</span>
            <span>Followed at</span>
            <span>Bookmark price</span>
            <span>Current price</span>
            <span>Growth / Loss</span>
            <span></span>
          </div>
          {bookmarks.map((bookmark) => (
            <div className="bookmark-row" key={bookmark.id}>
              <b>{bookmark.symbol}</b>
              <span>{new Date(bookmark.created_at).toLocaleString("en-IN")}</span>
              <span>₹{bookmark.bookmark_price.toLocaleString("en-IN")}</span>
              <span>{bookmark.current_price !== null ? `₹${bookmark.current_price.toLocaleString("en-IN")}` : "—"}</span>
              <span className={bookmark.growth_pct !== null && bookmark.growth_pct >= 0 ? "positive" : "negative"}>
                {bookmark.growth_pct !== null ? `${bookmark.growth_pct > 0 ? "+" : ""}${bookmark.growth_pct}%` : "—"}
              </span>
              <button className="follow-button following" onClick={() => void unfollow(bookmark.symbol)}>Unfollow</button>
            </div>
          ))}
        </section>
      )}
    </main>
  );
}
