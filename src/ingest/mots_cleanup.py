"""Move off-topic MOTS downloads out of data/raw/mots (reversible)."""

from __future__ import annotations

import shutil
import sys

from src.common.paths import RAW

OFFTOPIC_CATEGORIES_2018 = {55, 139, 260, 297, 335, 367, 466, 651, 773}


def main() -> int:
    mots = RAW / "mots"
    dest_root = RAW / "_offtopic" / "mots"
    moved = 0
    for cid in OFFTOPIC_CATEGORIES_2018:
        src = mots / "2018" / str(cid)
        if not src.exists():
            continue
        dest = dest_root / "2018" / str(cid)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            shutil.rmtree(dest)
        shutil.move(str(src), str(dest))
        moved += 1
    print(f"Moved {moved} off-topic category folders to {dest_root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
