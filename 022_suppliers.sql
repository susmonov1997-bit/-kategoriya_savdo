-- 022_suppliers.sql — ta'minotchilar: guruhlar, brend + kategoriya → ta'minotchi qoidalari, tovar istisnolari.
-- Ta'minotchilar kirim faylidagi nomlardan olinadi (wh_receipts.supplier). Tovarning ta'minotchisi:
--   1) tovar istisnosi (sup_product) → 2) qoida (sup_rules: brend + kategoriya) → 3) kirim tarixi (eng ko'p dona).
-- Natija product_supplier'da tayyor turadi (filtr va qirqim uchun) — suppliers.refresh() qayta quradi.

CREATE TABLE IF NOT EXISTS sup_groups (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS suppliers (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,            -- kirim faylidagi nom («(таьминотчи)»siz)
    group_id    INT REFERENCES sup_groups(id) ON DELETE SET NULL,
    confirmed   BOOLEAN NOT NULL DEFAULT false,  -- guruhi tasdiqlangan (aks holda — avtomatik taklif)
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- qo'lda belgilangan qoidalar (avtomatik qoidalar kirimdan hisoblanadi, bu yerda saqlanmaydi)
CREATE TABLE IF NOT EXISTS sup_rules (
    category_id INT  NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    brand       TEXT NOT NULL,
    supplier_id INT  NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
    updated_by  BIGINT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (category_id, brand)
);

-- tovar istisnosi (bir nechta ta'minotchidan kelgan tovar — asosiysi)
CREATE TABLE IF NOT EXISTS sup_product (
    product_id  BIGINT PRIMARY KEY REFERENCES products(product_id) ON DELETE CASCADE,
    supplier_id INT NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
    updated_by  BIGINT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- yakuniy natija: tovar → ta'minotchi, guruh (src: x — istisno, r — qoida, a — avtomatik qoida, k — kirim tarixi)
CREATE TABLE IF NOT EXISTS product_supplier (
    product_id  BIGINT PRIMARY KEY,
    supplier_id INT NOT NULL,
    group_id    INT,
    src         CHAR(1) NOT NULL
);

ALTER TABLE uploads DROP CONSTRAINT IF EXISTS uploads_kind_check;
ALTER TABLE uploads ADD CONSTRAINT uploads_kind_check
    CHECK (kind IN ('products','branches','sales','bonus','owners','plan','stock_branch','stock_wh','receipts','sup_rules'));

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['sup_groups','suppliers','sup_rules','sup_product','product_supplier'] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS trg_fact_bump ON %I', t);
        EXECUTE format('CREATE TRIGGER trg_fact_bump AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON %I '
                       'FOR EACH STATEMENT EXECUTE FUNCTION fact_bump_version()', t);
    END LOOP;
END $$;
