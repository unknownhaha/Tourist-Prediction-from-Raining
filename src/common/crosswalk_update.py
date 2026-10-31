"""Extend province_crosswalk.csv with new aliases from staged data."""

from __future__ import annotations

import pandas as pd

from src.common.paths import CONFIG, STAGING
from src.common.provinces import load_crosswalk


def extend_from_mots() -> int:
    path = STAGING / "mots" / "fact_tourism_long.parquet"
    if not path.exists():
        return 0
    df = pd.read_parquet(path)
    if "province_th" not in df.columns:
        return 0
    cw = load_crosswalk()
    known = set(cw["alias"].astype(str).str.strip()) | set(cw["province_th"].astype(str).str.strip())
    new_names = sorted({str(x).strip() for x in df["province_th"].dropna().unique() if str(x).strip() not in known})
    if not new_names:
        return 0
    rows = []
    from src.common.provinces import alias_to_province_id

    for name in new_names:
        pid = alias_to_province_id(name, cw)
        if not pid:
            continue
        rows.append(
            {
                "canonical_key": pid,
                "province_id": pid,
                "alias": name,
                "province_th": name,
                "province_en": "",
                "region": "",
            }
        )
    out = pd.concat([cw, pd.DataFrame(rows)], ignore_index=True)
    out.to_csv(CONFIG / "province_crosswalk.csv", index=False)
    return len(new_names)
