-- 012_names.sql — tovar nomi (variant nomi) savdo/qoldiq fayllaridan ham yangilanadi.
-- Nom sales_fact hisobiga ta'sir qilmaydi: faqat nom o'zgarsa — to'liq qayta qurish EMAS, faqat kesh versiyasi oshadi.
DROP TRIGGER IF EXISTS trg_fact_dirty ON products;
CREATE TRIGGER trg_fact_dirty AFTER INSERT OR DELETE OR TRUNCATE OR UPDATE OF product_id, category_id, brand, status, attrs
    ON products FOR EACH STATEMENT EXECUTE FUNCTION fact_mark_dirty();
DROP TRIGGER IF EXISTS trg_fact_bump_name ON products;
CREATE TRIGGER trg_fact_bump_name AFTER UPDATE OF name
    ON products FOR EACH STATEMENT EXECUTE FUNCTION fact_bump_version();
