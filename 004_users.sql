-- =====================================================================
-- 004_users.sql — Foydalanuvchilar: kirish so'rovi → admin tasdiqlaydi / rad etadi
--   role:   viewer   — Mini App'da ko'radi
--           uploader — ko'radi va fayl yuklaydi (bot va Mini App orqali)
--   status: pending | approved | rejected | revoked
-- ADMIN_IDS (Railway o'zgaruvchisi) dagilar har doim bosh admin — jadvalga bog'liq emas.
-- =====================================================================
CREATE TABLE IF NOT EXISTS users (
    tg_id         BIGINT PRIMARY KEY,
    first_name    TEXT,
    last_name     TEXT,
    username      TEXT,
    role          TEXT NOT NULL DEFAULT 'viewer' CHECK (role IN ('viewer','uploader')),
    status        TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected','revoked')),
    requested_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at    TIMESTAMPTZ,
    decided_by    BIGINT,
    last_seen_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_users_status ON users(status);
