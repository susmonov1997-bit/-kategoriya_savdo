-- 006: kompaniya jami savdosi (ulush uchun maxraj)
-- Savdo faylidagi BARCHA kategoriyalar (spravochnikda bo'lmaganlari ham), faqat «К» bilan belgilangan
-- kategoriyalar (masalan «"К" Б/У Смартфонлар») chiqarib tashlanadi. Saqlash: sana × filial.
-- Tannarx front marja bilan bir xil qoida: Сони × Кирим нархи × kurs (dollar) yoki × 1 (so'm).

CREATE TABLE IF NOT EXISTS company_daily (
    sale_date   DATE    NOT NULL,
    branch_id   INT     NOT NULL REFERENCES branches(id),
    qty         NUMERIC(16,3) NOT NULL DEFAULT 0,
    amount      NUMERIC(20,2) NOT NULL DEFAULT 0,
    cost        NUMERIC(20,2) NOT NULL DEFAULT 0,      -- Жами(Кирим нархи) (eski usul uchun)
    cost_usd    NUMERIC(20,4) NOT NULL DEFAULT 0,
    cost_uzs    NUMERIC(20,2) NOT NULL DEFAULT 0,
    cost_split  BOOLEAN NOT NULL DEFAULT FALSE,
    upload_id   INT     NOT NULL,
    PRIMARY KEY (sale_date, branch_id)
);

CREATE OR REPLACE VIEW company_enriched AS
SELECT c.sale_date, c.branch_id, c.qty, c.amount,
       CASE WHEN c.cost_split THEN c.cost_usd * COALESCE(f.rate, 0) + c.cost_uzs ELSE c.cost END AS cost
FROM company_daily c
LEFT JOIN fx_periods f ON c.sale_date >= f.valid_from AND c.sale_date < f.valid_to;
