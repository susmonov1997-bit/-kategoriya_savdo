-- 023_shipments.sql — ta'minotchi jo'natmalari (Artel SAP: «01-24_bi.xlsx»): Zakaz («в работе») va Yo'lda
-- (jo'natilgan − shu omborga kirim). Artel material kodi → Товар ИД: product_aliases ('am:<material>').

CREATE TABLE IF NOT EXISTS sup_ship (
    doc_no       BIGINT  NOT NULL,             -- Документ
    pos          INT     NOT NULL,             -- Поз.
    doc_date     DATE    NOT NULL,             -- ДатаДокум
    supplier     TEXT    NOT NULL,             -- ta'minotchi (kirimdagi nomi bilan: «Тошкент Artel КБТ»)
    material     TEXT    NOT NULL,             -- Материал (Artel kodi)
    mat_name     TEXT,                         -- Название (Artel nomi)
    product_id   BIGINT,                       -- bog'lanmagan bo'lsa NULL (Kutayotgan)
    qty          NUMERIC(14,3) NOT NULL,
    value_usd    NUMERIC(20,4),
    status       TEXT    NOT NULL,             -- work (в работе) | part (частично) | done (Исполнено)
    dest         TEXT,                         -- Номер заказа на поставку (matn)
    dest_key     TEXT,                         -- manzil kaliti (Коканд → коканд)
    warehouse_id INT REFERENCES warehouses(id),
    direct       BOOLEAN NOT NULL DEFAULT false,   -- filialning o'ziga (Наманган, Фаргона)
    base         TEXT,                         -- Artel bazasi (Даш / Бух)
    trade_house  TEXT,
    upload_id    INT     NOT NULL,
    PRIMARY KEY (doc_no, pos)
);
CREATE INDEX IF NOT EXISTS ix_sup_ship_date ON sup_ship (supplier, doc_date);
CREATE INDEX IF NOT EXISTS ix_sup_ship_pid ON sup_ship (product_id, warehouse_id);

ALTER TABLE uploads DROP CONSTRAINT IF EXISTS uploads_kind_check;
ALTER TABLE uploads ADD CONSTRAINT uploads_kind_check
    CHECK (kind IN ('products','branches','sales','bonus','owners','plan','stock_branch','stock_wh','receipts','sup_rules',
                    'ship','ship_map'));

DROP TRIGGER IF EXISTS trg_fact_bump ON sup_ship;
CREATE TRIGGER trg_fact_bump AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON sup_ship
    FOR EACH STATEMENT EXECUTE FUNCTION fact_bump_version();
