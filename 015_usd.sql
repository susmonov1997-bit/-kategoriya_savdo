-- 015_usd.sql — «So'm | $ | Dona»: savdo va kompaniya jamini dollarda ko'rsatish.
-- Har kun o'sha kunda amal qilgan bot kursi (/kurs, fx_periods) bilan o'tkaziladi.
CREATE OR REPLACE VIEW sales_enriched AS
SELECT s.sale_date, s.branch_id, s.product_id, s.is_bonus, s.qty, s.amount, t.cost, s.upload_id,
       p.category_id, p.brand,
       b.pct                                              AS bonus_pct,
       s.amount - t.cost                                  AS gross_margin,
       t.cost * b.pct / 100                               AS supplier_income,
       s.amount - t.cost + t.cost * b.pct / 100           AS margin,
       f.rate                                             AS rate
FROM sales_daily s
JOIN products p USING (product_id)
JOIN product_bonus_pct b USING (product_id)
LEFT JOIN fx_periods f ON s.sale_date >= f.valid_from AND s.sale_date < f.valid_to
CROSS JOIN LATERAL (
    SELECT CASE WHEN s.cost_split THEN s.cost_usd * COALESCE(f.rate, 0) + s.cost_uzs ELSE s.cost END AS cost
) t;

ALTER TABLE sales_fact ADD COLUMN IF NOT EXISTS rate FLOAT8;

CREATE OR REPLACE VIEW sales_fact_usd AS
SELECT sale_date, branch_id, region_id, cluster_id, product_id, is_bonus, qty,
       amount / NULLIF(rate, 0)          AS amount,
       gross_margin / NULLIF(rate, 0)    AS gross_margin,
       supplier_income / NULLIF(rate, 0) AS supplier_income,
       margin / NULLIF(rate, 0)          AS margin,
       rate
FROM sales_fact;

CREATE OR REPLACE VIEW company_enriched AS
SELECT c.sale_date, c.branch_id, c.qty, c.amount,
       CASE WHEN c.cost_split THEN c.cost_usd * COALESCE(f.rate, 0) + c.cost_uzs ELSE c.cost END AS cost,
       f.rate
FROM company_daily c
LEFT JOIN fx_periods f ON c.sale_date >= f.valid_from AND c.sale_date < f.valid_to;

CREATE OR REPLACE VIEW company_enriched_usd AS
SELECT sale_date, branch_id, qty, amount / NULLIF(rate, 0) AS amount, cost / NULLIF(rate, 0) AS cost, rate
FROM company_enriched;

-- sales_fact kurs ustuni bilan qayta quriladi
UPDATE fact_meta SET dirty = true WHERE id = 1;
