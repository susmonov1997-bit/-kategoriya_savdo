-- Tezlik: tayyor hisob-kitob jadvali. sales_enriched (kurs, bonus %, tannarx, marja) har so'rovda qayta
-- hisoblanmasligi uchun natija shu yerda float8 ko'rinishida saqlanadi (+ hudud/klaster).
-- Yangilanish:
--   * savdo yuklanganda — loader shu sanalarni o'zi qayta yozadi (fact.refresh_dates, o'sha tranzaksiyada);
--   * tovar/filial spravochnigi, bonus %, kurs o'zgarsa — trigger dirty=true qiladi, API/bot to'liq qayta quradi;
--   * har qanday ma'lumot o'zgarishi version ni oshiradi — API keshi shu bilan yangilanadi.
CREATE TABLE IF NOT EXISTS sales_fact (
    sale_date       DATE    NOT NULL,
    branch_id       INT     NOT NULL,
    region_id       INT,
    cluster_id      INT,
    product_id      BIGINT  NOT NULL,
    is_bonus        BOOLEAN NOT NULL,
    qty             FLOAT8  NOT NULL,
    amount          FLOAT8  NOT NULL,
    gross_margin    FLOAT8  NOT NULL,
    supplier_income FLOAT8  NOT NULL,
    margin          FLOAT8  NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_fact_date ON sales_fact (sale_date);
CREATE INDEX IF NOT EXISTS ix_fact_product_date ON sales_fact (product_id, sale_date);

CREATE TABLE IF NOT EXISTS fact_meta (
    id         INT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    version    BIGINT  NOT NULL DEFAULT 1,
    dirty      BOOLEAN NOT NULL DEFAULT true,
    rebuilt_at TIMESTAMPTZ
);
INSERT INTO fact_meta (id) VALUES (1) ON CONFLICT DO NOTHING;

CREATE OR REPLACE FUNCTION fact_mark_dirty() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN UPDATE fact_meta SET dirty = true, version = version + 1 WHERE id = 1; RETURN NULL; END $$;
CREATE OR REPLACE FUNCTION fact_bump_version() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN UPDATE fact_meta SET version = version + 1 WHERE id = 1; RETURN NULL; END $$;

DO $$
DECLARE t TEXT;
BEGIN
    -- marja/hudud hisobiga ta'sir qiladigan jadvallar → to'liq qayta qurish kerak
    FOREACH t IN ARRAY ARRAY['products','branches','brand_bonus','fx_rates'] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS trg_fact_dirty ON %I', t);
        EXECUTE format('CREATE TRIGGER trg_fact_dirty AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON %I '
                       'FOR EACH STATEMENT EXECUTE FUNCTION fact_mark_dirty()', t);
    END LOOP;
    -- faqat kesh yangilanishi kerak bo'lgan jadvallar
    FOREACH t IN ARRAY ARRAY['sales_daily','company_daily','sales_plan','categories','category_attributes',
                             'attribute_ranges','regions','clusters'] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS trg_fact_bump ON %I', t);
        EXECUTE format('CREATE TRIGGER trg_fact_bump AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON %I '
                       'FOR EACH STATEMENT EXECUTE FUNCTION fact_bump_version()', t);
    END LOOP;
END $$;
