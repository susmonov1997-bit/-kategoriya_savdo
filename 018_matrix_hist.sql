-- 018_matrix_hist.sql — matritsa versiyalari (kunlik chartlar uchun).
-- Matritsa o'zgargan kuni yangi versiya yoziladi (ver = shu sana). Birinchi versiya ver = 2000-01-01 —
-- o'tgan kunlar (versiyalar boshlanishidan oldin) hozirgi matritsa bilan hisoblanadi.
CREATE TABLE IF NOT EXISTS matrix_hist (
    ver          DATE    NOT NULL,
    branch_id    INT     NOT NULL,
    product_id   BIGINT  NOT NULL,
    category_id  INT     NOT NULL,
    need         NUMERIC(10,2) NOT NULL,
    PRIMARY KEY (ver, branch_id, product_id)
);
