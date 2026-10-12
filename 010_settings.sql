-- Ilova sozlamalari (kalit → qiymat). notify_muted = '1' — yuklashdan keyingi xabarlar o'chirilgan
-- (admin /xabar bilan boshqaradi); muted_from / muted_to — o'chirilgan paytda yuklangan savdo sanalari.
CREATE TABLE IF NOT EXISTS app_settings (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_by BIGINT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
