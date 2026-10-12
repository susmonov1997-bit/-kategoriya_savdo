-- Spravochnikda yo'q SKU / filial savdosi tashlab yuborilmaydi — shu yerda kutadi.
-- Tovar yoki filial spravochnigi yuklanganda (pending.resolve_pending) mos qatorlar sales_daily /
-- company_daily ga avtomatik ko'chiriladi — savdo faylini qayta yuklash shart emas.
-- Savdo shu sanalar uchun qayta yuklansa, bu sanalardagi kutayotgan qatorlar ham almashtiriladi.
CREATE TABLE IF NOT EXISTS sales_pending (
    sale_date    DATE    NOT NULL,
    branch_key   TEXT    NOT NULL,          -- name_key(Филиал)
    branch_name  TEXT,
    product_id   BIGINT  NOT NULL,
    product_name TEXT,
    category     TEXT,
    brand        TEXT,
    is_bonus     BOOLEAN NOT NULL DEFAULT false,
    qty          NUMERIC(14,3) NOT NULL,
    amount       NUMERIC(18,2) NOT NULL,
    cost         NUMERIC(18,2) NOT NULL,
    cost_usd     NUMERIC(18,4) NOT NULL DEFAULT 0,
    cost_uzs     NUMERIC(18,2) NOT NULL DEFAULT 0,
    cost_split   BOOLEAN NOT NULL DEFAULT false,
    upload_id    INT     NOT NULL,
    PRIMARY KEY (sale_date, branch_key, product_id, is_bonus)
);
CREATE INDEX IF NOT EXISTS ix_sales_pending_product ON sales_pending (product_id);

-- noma'lum filial bo'yicha set jami (company_daily uchun), «К»siz
CREATE TABLE IF NOT EXISTS company_pending (
    sale_date   DATE NOT NULL,
    branch_key  TEXT NOT NULL,
    branch_name TEXT,
    qty         NUMERIC(16,3) NOT NULL DEFAULT 0,
    amount      NUMERIC(20,2) NOT NULL DEFAULT 0,
    cost        NUMERIC(20,2) NOT NULL DEFAULT 0,
    cost_usd    NUMERIC(20,4) NOT NULL DEFAULT 0,
    cost_uzs    NUMERIC(20,2) NOT NULL DEFAULT 0,
    cost_split  BOOLEAN NOT NULL DEFAULT false,
    upload_id   INT NOT NULL,
    PRIMARY KEY (sale_date, branch_key)
);
