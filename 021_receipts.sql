-- 021_receipts.sql — «Ta'minot»: ta'minotchidan omborga kirim (kirim_tovarlar_*.xlsx) va signallar.
-- Faylda Товар Ид yo'q → tovar NOM bo'yicha bog'lanadi (aynan / taxminiy), qo'lda bog'langan nom va «Товар коди»
-- product_aliases'da eslab qolinadi. Bog'lanmaganlar receipt_pending'da kutadi.

CREATE TABLE IF NOT EXISTS wh_receipts (
    rec_date      DATE    NOT NULL,          -- Кабул вақти
    warehouse_id  INT     NOT NULL REFERENCES warehouses(id),
    product_id    BIGINT  NOT NULL REFERENCES products(product_id),
    order_code    TEXT    NOT NULL DEFAULT '',  -- Буюртма коди
    supplier      TEXT    NOT NULL DEFAULT '',  -- Таъминотчи («(таьминотчи)» olib tashlangan)
    qty           NUMERIC(14,3) NOT NULL,
    value_usd     NUMERIC(20,4),
    upload_id     INT     NOT NULL,
    PRIMARY KEY (rec_date, warehouse_id, product_id, order_code, supplier)
);
CREATE INDEX IF NOT EXISTS ix_wh_receipts_product ON wh_receipts (product_id, rec_date);

CREATE TABLE IF NOT EXISTS receipt_pending (
    rec_date      DATE    NOT NULL,
    loc_key       TEXT    NOT NULL,          -- ombor bo'limi kaliti (warehouse_aliases kabi)
    loc_name      TEXT,
    prod_key      TEXT    NOT NULL,          -- name_key(tovar nomi)
    product_code  TEXT,                       -- Товар коди (PC…)
    product_name  TEXT,
    category      TEXT,
    brand         TEXT,
    order_code    TEXT    NOT NULL DEFAULT '',
    supplier      TEXT    NOT NULL DEFAULT '',
    qty           NUMERIC(14,3) NOT NULL,
    value_usd     NUMERIC(20,4),
    product_id    BIGINT,                     -- tovar topilgan, faqat ombor bo'limi noma'lum bo'lsa
    upload_id     INT     NOT NULL,
    PRIMARY KEY (rec_date, loc_key, prod_key, order_code, supplier)
);

-- qo'lda bog'langan nomlar va kodlar: 'n:<name_key>' / 'c:<Товар коди>' → product_id
CREATE TABLE IF NOT EXISTS product_aliases (
    alias_key   TEXT PRIMARY KEY,
    product_id  BIGINT NOT NULL REFERENCES products(product_id) ON DELETE CASCADE,
    created_by  BIGINT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Ta'minot signallari (Mini App «⚠» + bot)
CREATE TABLE IF NOT EXISTS supply_signals (
    id          SERIAL PRIMARY KEY,
    kind        TEXT NOT NULL,               -- kirim_nomx — matritsada yo'q tovar kirimi
    skey        TEXT NOT NULL UNIQUE,        -- takrorlanmasligi uchun kalit
    warehouse_id INT,
    product_id  BIGINT,
    data        JSONB NOT NULL DEFAULT '{}'::jsonb,
    upload_id   INT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    notified_at TIMESTAMPTZ,
    seen_at     TIMESTAMPTZ,
    seen_by     BIGINT,
    note        TEXT
);
CREATE INDEX IF NOT EXISTS ix_supply_signals_new ON supply_signals (created_at DESC);

ALTER TABLE uploads DROP CONSTRAINT IF EXISTS uploads_kind_check;
ALTER TABLE uploads ADD CONSTRAINT uploads_kind_check
    CHECK (kind IN ('products','branches','sales','bonus','owners','plan','stock_branch','stock_wh','receipts'));

-- kirim o'zgarsa API keshi yangilansin
DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['wh_receipts','receipt_pending','product_aliases','supply_signals'] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS trg_fact_bump ON %I', t);
        EXECUTE format('CREATE TRIGGER trg_fact_bump AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON %I '
                       'FOR EACH STATEMENT EXECUTE FUNCTION fact_bump_version()', t);
    END LOOP;
END $$;
