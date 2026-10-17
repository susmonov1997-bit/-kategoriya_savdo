-- =====================================================================
-- 011_stock.sql — Hududiy omborlar, filial va ombor qoldig'i (snapshot), yangi filial/ombor so'rovlari
-- Qoldiq — holat: sana × joy × SKU. Bir sana qayta yuklansa, fayldagi joylar uchun shu sana almashtiriladi.
-- =====================================================================

-- Hududiy omborlar (5 ta). Yangi ombor — filial spravochnigidagi «Омбор» ustuni orqali qo'shiladi.
CREATE TABLE IF NOT EXISTS warehouses (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,             -- «Гузор омбори»
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Ombor nomlarining turli yozilishlari → ombor.
--   alias_key = name_key(nom yoki uning boshidagi so'z(lar)i): «g'uzor», «гузор», «гузор омбори» …
--   Qoldiq faylidagi «G'uzor Logist ombor», «G'uzor Artel ombor», «G'uzor citystar ombor» → «g'uzor» → Гузор омбори
CREATE TABLE IF NOT EXISTS warehouse_aliases (
    alias_key     TEXT PRIMARY KEY,
    warehouse_id  INT  NOT NULL REFERENCES warehouses(id) ON DELETE CASCADE,
    created_by    BIGINT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE branches ADD COLUMN IF NOT EXISTS warehouse_id INT REFERENCES warehouses(id);

INSERT INTO warehouses (name) VALUES
    ('Самарканд омбори'), ('Кукон омбори'), ('Гузор омбори'), ('Охангарон омбори'), ('Хоразм омбори')
ON CONFLICT (name) DO NOTHING;

INSERT INTO warehouse_aliases (alias_key, warehouse_id)
SELECT v.alias, w.id
FROM (VALUES ('самарканд омбори', 'Самарканд омбори'), ('самарканд', 'Самарканд омбори'), ('samarqand', 'Самарканд омбори'),
             ('кукон омбори', 'Кукон омбори'), ('кукон', 'Кукон омбори'), ('qo''qonboy', 'Кукон омбори'),
             ('гузор омбори', 'Гузор омбори'), ('гузор', 'Гузор омбори'), ('g''uzor', 'Гузор омбори'),
             ('охангарон омбори', 'Охангарон омбори'), ('охангарон', 'Охангарон омбори'), ('ohangaron', 'Охангарон омбори'),
             ('хоразм омбори', 'Хоразм омбори'), ('хоразм', 'Хоразм омбори'), ('xorazmbaza', 'Хоразм омбори')) AS v(alias, wh)
JOIN warehouses w ON w.name = v.wh
ON CONFLICT (alias_key) DO NOTHING;

-- ---------------------------------------------------------------------
-- Filial qoldig'i (Tovar_qoldigi_DD.MM.xlsx): Filial × Tovar Id → Qoldiq
--   cost_uzs  = Σ Qoldiq × «Kirim So'm»   (tannarx, so'm)
--   price_uzs = Σ Qoldiq × «Sotish narxi» (sotish narxida, so'm)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stock_branch (
    snap_date   DATE    NOT NULL,
    branch_id   INT     NOT NULL REFERENCES branches(id),
    product_id  BIGINT  NOT NULL REFERENCES products(product_id),
    qty         NUMERIC(14,3) NOT NULL,
    cost_uzs    NUMERIC(20,2),
    price_uzs   NUMERIC(20,2),
    upload_id   INT     NOT NULL,
    PRIMARY KEY (snap_date, branch_id, product_id)
);
CREATE INDEX IF NOT EXISTS ix_stock_branch_product ON stock_branch (product_id, snap_date);

-- ---------------------------------------------------------------------
-- Ombor qoldig'i (Ombor qoldig'i DD-MM-YYYY.xlsx): Омбор × ID → «Мижоз учун қолдиқ» (rezervsiz)
--   Bo'limlar (Logist / Artel / citystar) bitta omborga yig'iladi.
--   value_usd = Σ qoldiq × «A сектор учун жорий сотиш нархи» ($); so'mda — botga yuklangan kurs bilan (view).
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stock_wh (
    snap_date     DATE    NOT NULL,
    warehouse_id  INT     NOT NULL REFERENCES warehouses(id),
    product_id    BIGINT  NOT NULL REFERENCES products(product_id),
    qty           NUMERIC(14,3) NOT NULL,
    value_usd     NUMERIC(20,4),
    upload_id     INT     NOT NULL,
    PRIMARY KEY (snap_date, warehouse_id, product_id)
);
CREATE INDEX IF NOT EXISTS ix_stock_wh_product ON stock_wh (product_id, snap_date);

CREATE OR REPLACE VIEW stock_wh_enriched AS
SELECT s.snap_date, s.warehouse_id, s.product_id, s.qty, s.value_usd,
       s.value_usd * f.rate AS value_uzs, f.rate
FROM stock_wh s
LEFT JOIN fx_periods f ON s.snap_date >= f.valid_from AND s.snap_date < f.valid_to;

-- Spravochnikda yo'q SKU / filial / ombor bo'limi qoldig'i — shu yerda kutadi, keyin avtomatik ulanadi.
--   kind = 'b' (filial: loc_key = name_key(Filial)) | 'w' (ombor: loc_key = ombor nomi kaliti yoki birinchi so'zi)
CREATE TABLE IF NOT EXISTS stock_pending (
    kind          CHAR(1) NOT NULL CHECK (kind IN ('b','w')),
    snap_date     DATE    NOT NULL,
    loc_key       TEXT    NOT NULL,
    loc_name      TEXT,
    region        TEXT,                 -- filial faylidagi «Hudud» (yangi filial uchun)
    product_id    BIGINT  NOT NULL,
    product_name  TEXT,
    category      TEXT,
    brand         TEXT,
    qty           NUMERIC(14,3) NOT NULL,
    cost_uzs      NUMERIC(20,2),
    price_uzs     NUMERIC(20,2),
    value_usd     NUMERIC(20,4),
    upload_id     INT     NOT NULL,
    PRIMARY KEY (kind, snap_date, loc_key, product_id)
);

-- ---------------------------------------------------------------------
-- Bot so'rovlari: yangi filial (ombor → klaster → hudud) va noma'lum ombor bo'limi
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS branch_requests (
    id            SERIAL PRIMARY KEY,
    name_key      TEXT NOT NULL UNIQUE,
    name          TEXT NOT NULL,
    region_hint   TEXT,                 -- fayldagi hudud nomi (bo'lsa)
    warehouse_id  INT REFERENCES warehouses(id),
    cluster_id    INT REFERENCES clusters(id),
    region_id     INT REFERENCES regions(id),
    notified_at   TIMESTAMPTZ,
    done_at       TIMESTAMPTZ,
    done_by       BIGINT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS warehouse_requests (
    id            SERIAL PRIMARY KEY,
    alias_key     TEXT NOT NULL UNIQUE,  -- so'raladigan kalit (nomning birinchi so'zi)
    sample_name   TEXT NOT NULL,         -- fayldagi to'liq nom
    notified_at   TIMESTAMPTZ,
    done_at       TIMESTAMPTZ,
    done_by       BIGINT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE uploads DROP CONSTRAINT IF EXISTS uploads_kind_check;
ALTER TABLE uploads ADD CONSTRAINT uploads_kind_check
    CHECK (kind IN ('products','branches','sales','bonus','owners','plan','stock_branch','stock_wh'));

-- qoldiq o'zgarsa API keshi yangilansin (sales_fact qayta qurilmaydi — faqat versiya)
DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['stock_branch','stock_wh','warehouses','warehouse_aliases'] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS trg_fact_bump ON %I', t);
        EXECUTE format('CREATE TRIGGER trg_fact_bump AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON %I '
                       'FOR EACH STATEMENT EXECUTE FUNCTION fact_bump_version()', t);
    END LOOP;
END $$;
