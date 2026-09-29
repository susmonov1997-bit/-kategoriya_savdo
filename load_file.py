"""Botsiz yuklash — katta (oylik/tarixiy) fayllar va boshlang'ich yuklash uchun.
Fayl turi avtomatik aniqlanadi. Bir nechta faylni tartib bilan berish mumkin:

    python load_file.py хусусиятлар.xlsx "Filial spravochnigi.xlsx" "Chiqim tovarlar 01.09-28.09.xlsx"
"""
import asyncio
import html
import re
import sys
import time

from db import create_pool, migrate
from loaders_common import LoaderError
from loaders_dispatch import load_any


async def main(paths: list[str]) -> int:
    pool = await create_pool()
    await migrate(pool)
    rc = 0
    for path in paths:
        t0 = time.time()
        print(f"\n=== {path}")
        with open(path, "rb") as f:
            data = f.read()
        try:
            text = await load_any(pool, data, path.split("/")[-1], None)
            print(html.unescape(re.sub(r"</?(b|i|code)>", "", text)))
        except LoaderError as e:
            print(f"❌ Yuklanmadi: {e}")
            rc = 1
        print(f"({time.time() - t0:.1f} s)")
    await pool.close()
    return rc


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    sys.exit(asyncio.run(main(sys.argv[1:])))
