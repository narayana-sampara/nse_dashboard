CREATE TABLE IF NOT EXISTS users (
    id BIGSERIAL PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'user')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS user_menu_permissions (
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    menu_key TEXT NOT NULL CHECK (menu_key IN ('signals', 'weekly', 'monthly', 'radar', 'future', 'analysis', 'five_percent_strategy')),
    PRIMARY KEY (user_id, menu_key)
);
