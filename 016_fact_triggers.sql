-- 016_fact_triggers.sql — sales_fact faqat haqiqatan kerak bo'lganda to'liq qayta quriladi.
-- Avval tovar/filial spravochnigining HAR yuklanishi (o'zgarish bo'lmasa ham) butun sales_fact'ni qayta qurardi
-- (millionlab qator — o'nlab soniya; shu paytda Mini App kutib turardi).
-- sales_fact tovardan faqat brend va kategoriyaga (qo'shimcha daromad %), filialdan — hudud va klasterga bog'liq.

-- products
DROP TRIGGER IF EXISTS trg_fact_dirty ON products;
DROP TRIGGER IF EXISTS trg_fact_bump_name ON products;
DROP TRIGGER IF EXISTS trg_fact_dirty_row ON products;
DROP TRIGGER IF EXISTS trg_fact_dirty_del ON products;
DROP TRIGGER IF EXISTS trg_fact_bump ON products;
CREATE TRIGGER trg_fact_dirty_row AFTER UPDATE OF brand, category_id ON products
    FOR EACH ROW WHEN (OLD.brand IS DISTINCT FROM NEW.brand OR OLD.category_id IS DISTINCT FROM NEW.category_id)
    EXECUTE FUNCTION fact_mark_dirty();
CREATE TRIGGER trg_fact_dirty_del AFTER DELETE OR TRUNCATE ON products
    FOR EACH STATEMENT EXECUTE FUNCTION fact_mark_dirty();
CREATE TRIGGER trg_fact_bump AFTER INSERT OR UPDATE ON products      -- nom, status, xususiyat — faqat kesh
    FOR EACH STATEMENT EXECUTE FUNCTION fact_bump_version();

-- branches
DROP TRIGGER IF EXISTS trg_fact_dirty ON branches;
DROP TRIGGER IF EXISTS trg_fact_dirty_row ON branches;
DROP TRIGGER IF EXISTS trg_fact_dirty_del ON branches;
DROP TRIGGER IF EXISTS trg_fact_bump ON branches;
CREATE TRIGGER trg_fact_dirty_row AFTER UPDATE OF region_id, cluster_id ON branches
    FOR EACH ROW WHEN (OLD.region_id IS DISTINCT FROM NEW.region_id OR OLD.cluster_id IS DISTINCT FROM NEW.cluster_id)
    EXECUTE FUNCTION fact_mark_dirty();
CREATE TRIGGER trg_fact_dirty_del AFTER DELETE OR TRUNCATE ON branches
    FOR EACH STATEMENT EXECUTE FUNCTION fact_mark_dirty();
CREATE TRIGGER trg_fact_bump AFTER INSERT OR UPDATE ON branches      -- nom, ombor — faqat kesh
    FOR EACH STATEMENT EXECUTE FUNCTION fact_bump_version();
