-- =====================================================================
-- 003_brand_bonus.sql — Postavshiklardan qo'shimcha daromad (dop doxod + retro + kompensatsiya)
--   Brend × kategoriya kesimida bitta jami %.
--   Qo'shimcha daromad (so'm) = Жами(Кирим нархи) × % / 100   (bonus qatorlar ham kiradi)
--   Valovka = savdo − tannarx;   Marja = valovka + qo'shimcha daromad
-- Foizlar hisob paytida qo'llanadi: spravochnik qayta yuklansa, butun tarix yangi % bilan hisoblanadi.
-- =====================================================================

CREATE TABLE IF NOT EXISTS brand_bonus (
    id           SERIAL PRIMARY KEY,
    category_id  INT REFERENCES categories(id) ON DELETE CASCADE,   -- NULL = brendning barcha kategoriyalari
    brand        TEXT NOT NULL,                                     -- products.brand bilan aynan bir xil yoziladi
    pct          NUMERIC(6,3) NOT NULL CHECK (pct >= 0 AND pct <= 100),
    upload_id    INT,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_brand_bonus ON brand_bonus (brand, COALESCE(category_id, 0));

-- Amaldagi % har bir SKU uchun: avval brend×kategoriya, bo'lmasa brend (barcha kategoriyalar), bo'lmasa 0
CREATE OR REPLACE VIEW product_bonus_pct AS
SELECT p.product_id,
       COALESCE(bc.pct, ba.pct, 0) AS pct
FROM products p
LEFT JOIN brand_bonus bc ON bc.brand = p.brand AND bc.category_id = p.category_id
LEFT JOIN brand_bonus ba ON ba.brand = p.brand AND ba.category_id IS NULL;

-- API va hisobotlar shu view'dan o'qiydi
CREATE OR REPLACE VIEW sales_enriched AS
SELECT s.sale_date, s.branch_id, s.product_id, s.is_bonus, s.qty, s.amount, s.cost, s.upload_id,
       p.category_id, p.brand,
       b.pct                                              AS bonus_pct,
       s.amount - s.cost                                  AS gross_margin,     -- valovka
       s.cost * b.pct / 100                               AS supplier_income,  -- qo'shimcha daromad
       s.amount - s.cost + s.cost * b.pct / 100           AS margin            -- yakuniy marja
FROM sales_daily s
JOIN products p USING (product_id)
JOIN product_bonus_pct b USING (product_id);

-- uploads.kind ga 'bonus' qo'shiladi
ALTER TABLE uploads DROP CONSTRAINT IF EXISTS uploads_kind_check;
ALTER TABLE uploads ADD CONSTRAINT uploads_kind_check CHECK (kind IN ('products','branches','sales','bonus'));
