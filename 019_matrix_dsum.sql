-- 019_matrix_dsum.sql — kunlik chartlar uchun oldindan hisoblangan kunlik yig'indilar (har sana bir marta).
-- mx_dsum — matritsa bajarilishi: sana × filial × kategoriya × brend.
-- mx_osum — nematritsa qoldig'i: b (filial) / w (ombor) × sana × joy × kategoriya × brend.
-- mx_dsum_meta — qaysi sana (m/b/w) hisoblangan; qoldiq o'zgarsa yoki matritsa yangi versiyasi chiqsa o'chiriladi.
CREATE TABLE IF NOT EXISTS mx_dsum (
    snap_date    DATE NOT NULL,
    branch_id    INT  NOT NULL,
    category_id  INT  NOT NULL,
    brand        TEXT NOT NULL,
    pos          INT  NOT NULL,
    ok           INT  NOT NULL,
    miss         INT  NOT NULL,
    PRIMARY KEY (snap_date, branch_id, category_id, brand)
);
CREATE TABLE IF NOT EXISTS mx_osum (
    kind         CHAR(1) NOT NULL,      -- b | w
    snap_date    DATE NOT NULL,
    loc_id       INT  NOT NULL,         -- filial yoki ombor
    category_id  INT  NOT NULL,
    brand        TEXT NOT NULL,
    qty          FLOAT8 NOT NULL,
    usd          FLOAT8 NOT NULL,
    PRIMARY KEY (kind, snap_date, loc_id, category_id, brand)
);
CREATE TABLE IF NOT EXISTS mx_dsum_meta (
    kind         CHAR(1) NOT NULL,      -- m | b | w
    snap_date    DATE NOT NULL,
    computed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (kind, snap_date)
);

-- qoldiq yuklansa/almashsa — shu sanalar qayta hisoblanadi
CREATE OR REPLACE FUNCTION mx_dsum_inval_b() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    DELETE FROM mx_dsum_meta WHERE kind IN ('m', 'b') AND snap_date IN (SELECT DISTINCT snap_date FROM tt);
    RETURN NULL;
END $$;
CREATE OR REPLACE FUNCTION mx_dsum_inval_w() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    DELETE FROM mx_dsum_meta WHERE kind = 'w' AND snap_date IN (SELECT DISTINCT snap_date FROM tt);
    RETURN NULL;
END $$;
DROP TRIGGER IF EXISTS trg_mx_dsum_ins ON stock_branch;
DROP TRIGGER IF EXISTS trg_mx_dsum_del ON stock_branch;
DROP TRIGGER IF EXISTS trg_mx_dsum_upd ON stock_branch;
CREATE TRIGGER trg_mx_dsum_ins AFTER INSERT ON stock_branch REFERENCING NEW TABLE AS tt
    FOR EACH STATEMENT EXECUTE FUNCTION mx_dsum_inval_b();
CREATE TRIGGER trg_mx_dsum_del AFTER DELETE ON stock_branch REFERENCING OLD TABLE AS tt
    FOR EACH STATEMENT EXECUTE FUNCTION mx_dsum_inval_b();
CREATE TRIGGER trg_mx_dsum_upd AFTER UPDATE ON stock_branch REFERENCING NEW TABLE AS tt
    FOR EACH STATEMENT EXECUTE FUNCTION mx_dsum_inval_b();
DROP TRIGGER IF EXISTS trg_mx_dsum_ins ON stock_wh;
DROP TRIGGER IF EXISTS trg_mx_dsum_del ON stock_wh;
DROP TRIGGER IF EXISTS trg_mx_dsum_upd ON stock_wh;
CREATE TRIGGER trg_mx_dsum_ins AFTER INSERT ON stock_wh REFERENCING NEW TABLE AS tt
    FOR EACH STATEMENT EXECUTE FUNCTION mx_dsum_inval_w();
CREATE TRIGGER trg_mx_dsum_del AFTER DELETE ON stock_wh REFERENCING OLD TABLE AS tt
    FOR EACH STATEMENT EXECUTE FUNCTION mx_dsum_inval_w();
CREATE TRIGGER trg_mx_dsum_upd AFTER UPDATE ON stock_wh REFERENCING NEW TABLE AS tt
    FOR EACH STATEMENT EXECUTE FUNCTION mx_dsum_inval_w();

-- tovar brendi/kategoriyasi yoki filial ombori o'zgarsa — hammasi qayta
CREATE OR REPLACE FUNCTION mx_dsum_reset() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    DELETE FROM mx_dsum_meta;
    RETURN NULL;
END $$;
DROP TRIGGER IF EXISTS trg_mx_dsum_reset ON products;
CREATE TRIGGER trg_mx_dsum_reset AFTER UPDATE OF brand, category_id ON products
    FOR EACH ROW WHEN (OLD.brand IS DISTINCT FROM NEW.brand OR OLD.category_id IS DISTINCT FROM NEW.category_id)
    EXECUTE FUNCTION mx_dsum_reset();
DROP TRIGGER IF EXISTS trg_mx_dsum_reset ON branches;
CREATE TRIGGER trg_mx_dsum_reset AFTER UPDATE OF warehouse_id ON branches
    FOR EACH ROW WHEN (OLD.warehouse_id IS DISTINCT FROM NEW.warehouse_id)
    EXECUTE FUNCTION mx_dsum_reset();
