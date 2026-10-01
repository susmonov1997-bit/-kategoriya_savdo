-- 005: dollar kursi bilan front marja, mas'ul xodimlar, KBT/MBT guruhlari

-- ---------------------------------------------------------------------
-- Dollar kursi: valid_from sanasidan boshlab amal qiladi (keyingi yozuvgacha)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fx_rates (
    valid_from  DATE PRIMARY KEY,
    rate        NUMERIC(14,4) NOT NULL CHECK (rate > 0),
    set_by      BIGINT,
    set_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO fx_rates (valid_from, rate) VALUES ('2000-01-01', 11800) ON CONFLICT DO NOTHING;

CREATE OR REPLACE VIEW fx_periods AS
SELECT valid_from,
       COALESCE(lead(valid_from) OVER (ORDER BY valid_from), DATE '9999-12-31') AS valid_to,
       rate
FROM fx_rates;

-- ---------------------------------------------------------------------
-- Savdo: tannarx valyuta bo'yicha ajratib saqlanadi
--   cost_usd = Σ Сони × Кирим нархи   (Кирим нархи(Валюта тури) = Доллар)
--   cost_uzs = Σ Сони × Кирим нархи   (Сум)
--   cost_split = FALSE — eski yuklangan qatorlar (faqat Жами(Кирим нархи) bor)
-- ---------------------------------------------------------------------
ALTER TABLE sales_daily ADD COLUMN IF NOT EXISTS cost_usd   NUMERIC(18,4) NOT NULL DEFAULT 0;
ALTER TABLE sales_daily ADD COLUMN IF NOT EXISTS cost_uzs   NUMERIC(18,2) NOT NULL DEFAULT 0;
ALTER TABLE sales_daily ADD COLUMN IF NOT EXISTS cost_split BOOLEAN       NOT NULL DEFAULT FALSE;

DROP VIEW IF EXISTS sales_enriched;
CREATE VIEW sales_enriched AS
SELECT s.sale_date, s.branch_id, s.product_id, s.is_bonus, s.qty, s.amount, t.cost, s.upload_id,
       p.category_id, p.brand,
       b.pct                                              AS bonus_pct,
       s.amount - t.cost                                  AS gross_margin,     -- front marja
       t.cost * b.pct / 100                               AS supplier_income,  -- qo'shimcha daromad
       s.amount - t.cost + t.cost * b.pct / 100           AS margin            -- gross marja
FROM sales_daily s
JOIN products p USING (product_id)
JOIN product_bonus_pct b USING (product_id)
LEFT JOIN fx_periods f ON s.sale_date >= f.valid_from AND s.sale_date < f.valid_to
CROSS JOIN LATERAL (
    SELECT CASE WHEN s.cost_split THEN s.cost_usd * COALESCE(f.rate, 0) + s.cost_uzs ELSE s.cost END AS cost
) t;

-- ---------------------------------------------------------------------
-- Mas'ul xodimlar va guruhlar
-- ---------------------------------------------------------------------
ALTER TABLE categories ADD COLUMN IF NOT EXISTS owner TEXT;
UPDATE categories SET grp = upper(grp) WHERE grp IS NOT NULL AND grp <> upper(grp);

CREATE TABLE IF NOT EXISTS category_owner_list (
    name_key   TEXT PRIMARY KEY,      -- kategoriya nomi kaliti (loaders_common.name_key)
    category   TEXT NOT NULL,
    grp        TEXT,
    owner      TEXT NOT NULL,
    upload_id  INT
);

ALTER TABLE uploads DROP CONSTRAINT IF EXISTS uploads_kind_check;
ALTER TABLE uploads ADD CONSTRAINT uploads_kind_check
    CHECK (kind IN ('products','branches','sales','bonus','owners'));
