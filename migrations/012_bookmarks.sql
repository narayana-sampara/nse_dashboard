CREATE TABLE IF NOT EXISTS stock_bookmarks (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    symbol TEXT NOT NULL,
    bookmark_price DOUBLE PRECISION NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, symbol)
);

CREATE INDEX IF NOT EXISTS stock_bookmarks_user_idx
    ON stock_bookmarks (user_id, created_at DESC);
