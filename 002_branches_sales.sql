-- =====================================================================
-- 002_branches_sales.sql — Filiallar (Hudud, Klaster), kunlik savdo, xususiyat diapazonlari
-- =====================================================================

-- Hudud (Территория) va Klaster mustaqil o'lchovlar: klaster hududga bo'ysunmaydi.
CREATE TABLE IF NOT EXISTS regions (
    id    SERIAL PRIMARY KEY,
    name  TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS clusters (
    id    SERIAL PRIMARY KEY,
    name  TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS branches (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,                  -- Филиал (faylda qanday bo'lsa)
    name_key    TEXT NOT NULL UNIQUE,           -- solishtirish kaliti (apostrof/bo'shliq/registr birxil)
    region_id   INT  NOT NULL REFERENCES regions(id),
    cluster_id  INT  NOT NULL REFERENCES clusters(id),
    upload_id   INT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_branches_region  ON branches(region_id);
CREATE INDEX IF NOT EXISTS ix_branches_cluster ON branches(cluster_id);

-- Kunlik savdo: sana × filial × SKU × bonus belgisi bo'yicha jamlangan.
--   amount = Жами(Чиқим нархи),  cost = Жами(Кирим нархи),  marja = amount - cost
CREATE TABLE IF NOT EXISTS sales_daily (
    sale_date   DATE    NOT NULL,
    branch_id   INT     NOT NULL REFERENCES branches(id),
    product_id  BIGINT  NOT NULL REFERENCES products(product_id),
    is_bonus    BOOLEAN NOT NULL DEFAULT FALSE,
    qty         NUMERIC(14,3) NOT NULL,
    amount      NUMERIC(18,2) NOT NULL,
    cost        NUMERIC(18,2) NOT NULL,
    upload_id   INT     NOT NULL,
    PRIMARY KEY (sale_date, branch_id, product_id, is_bonus)
);
CREATE INDEX IF NOT EXISTS ix_sales_product_date ON sales_daily(product_id, sale_date);
CREATE INDEX IF NOT EXISTS ix_sales_branch_date  ON sales_daily(branch_id, sale_date);

-- Raqamli xususiyatlar uchun diapazonlar (filtr va qirqim shular bo'yicha).
-- Qiymat v diapazonga tushadi: (lo IS NULL OR v > lo) AND (hi IS NULL OR v <= hi)
CREATE TABLE IF NOT EXISTS attribute_ranges (
    category_id  INT      NOT NULL,
    slot         SMALLINT NOT NULL,
    sort_order   SMALLINT NOT NULL,
    label        TEXT     NOT NULL,
    lo           NUMERIC,
    hi           NUMERIC,
    PRIMARY KEY (category_id, slot, sort_order),
    FOREIGN KEY (category_id, slot) REFERENCES category_attributes(category_id, slot) ON DELETE CASCADE
);

-- Sentyabr 2026 savdosi taqsimoti asosida taklif qilingan diapazonlar (kodsiz o'zgartiriladi)
INSERT INTO attribute_ranges (category_id, slot, sort_order, label, lo, hi)
SELECT c.id, v.slot, v.ord, v.label, v.lo, v.hi
FROM categories c
JOIN (VALUES
    ('Совутгичлар', 1, 1, '≤150 sm',     NULL, 150),
    ('Совутгичлар', 1, 2, '151–175 sm',  150,  175),
    ('Совутгичлар', 1, 3, '176–190 sm',  175,  190),
    ('Совутгичлар', 1, 4, '>190 sm',     190,  NULL),
    ('Совутгичлар', 2, 1, '≤180 l',      NULL, 180),
    ('Совутгичлар', 2, 2, '181–220 l',   180,  220),
    ('Совутгичлар', 2, 3, '221–260 l',   220,  260),
    ('Совутгичлар', 2, 4, '261–320 l',   260,  320),
    ('Совутгичлар', 2, 5, '>320 l',      320,  NULL),
    ('Совутгичлар', 3, 1, 'Yo''q (0)',   NULL, 0),
    ('Совутгичлар', 3, 2, '1–50 l',      0,    50),
    ('Совутгичлар', 3, 3, '51–80 l',     50,   80),
    ('Совутгичлар', 3, 4, '81–110 l',    80,   110),
    ('Совутгичлар', 3, 5, '>110 l',      110,  NULL),
    ('Музлатгичлар',1, 1, '≤250 l',      NULL, 250),
    ('Музлатгичлар',1, 2, '251–350 l',   250,  350),
    ('Музлатгичлар',1, 3, '351–450 l',   350,  450),
    ('Музлатгичлар',1, 4, '>450 l',      450,  NULL)
) AS v(cat, slot, ord, label, lo, hi) ON v.cat = c.name
ON CONFLICT DO NOTHING;
