"""Province name crosswalk lookups."""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from src.common.paths import CONFIG, RAW
from src.common.province_metadata import EN_BY_ID, EXTRA_ALIASES, REGION_BY_ID

REGION_SHEET_PREFIXES = (
    "ภาค",
    "รวมภาค",
    "รวมปี",
    "ประเทศ",
    "Internal",
    "Guest Arrivals",
    "รวมทั้ง",
    "ที่มา",
    "R/Update",
    "ยอดสะสม",
    "ผ่านกำกับ",
)


def normalize_name(name: str) -> str:
    s = str(name).strip()
    s = re.sub(r"^\d+(\.\d+)?\.?\s*", "", s)
    s = re.sub(r"^รวม\s*", "", s)
    s = re.sub(r"\s*256[0-9].*$", "", s)
    s = re.sub(r"\s*Q[1-4].*$", "", s, flags=re.I)
    s = re.sub(r"\s*_\s*256[0-9].*$", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def load_crosswalk() -> pd.DataFrame:
    path = CONFIG / "province_crosswalk.csv"
    if not path.exists():
        raise FileNotFoundError("Missing config/province_crosswalk.csv")
    return pd.read_csv(path, dtype={"province_id": "string", "canonical_key": "string"})


def _canonical_by_id(cw: pd.DataFrame) -> dict[str, str]:
    canon = cw.dropna(subset=["province_id"]).copy()
    canon = canon[canon["province_id"].astype(str).str.strip() != ""]
    out: dict[str, str] = {}
    for pid, grp in canon.groupby("province_id"):
        pid_s = str(int(float(pid))) if str(pid).replace(".", "").isdigit() else str(pid).strip()
        th = grp["province_th"].astype(str).str.strip()
        canonical = th.iloc[0]
        for t in th:
            if t == grp["alias"].astype(str).str.strip().iloc[0] or len(t) >= len(canonical):
                canonical = t
        out[pid_s] = canonical
    return out


def alias_to_province_id(alias: str, crosswalk: pd.DataFrame | None = None) -> str | None:
    cw = crosswalk if crosswalk is not None else load_crosswalk()
    key = normalize_name(alias)
    if not key or key.lower() in ("nan", "none"):
        return None
    for col in ("alias", "province_th"):
        hit = cw[cw[col].astype(str).map(normalize_name) == key]
        if not hit.empty:
            pid = hit.iloc[0]["province_id"]
            if pd.isna(pid) or str(pid).strip() == "":
                return None
            try:
                return str(int(float(pid)))
            except (TypeError, ValueError):
                return str(pid).strip()
    return None


def classify_sheet(sheet_name: str, crosswalk: pd.DataFrame | None = None) -> str:
    """Return province, sub_area, or region."""
    cw = crosswalk if crosswalk is not None else load_crosswalk()
    raw = str(sheet_name).strip()
    norm = normalize_name(raw)
    if not norm:
        return "region"
    if any(raw.startswith(p) or norm.startswith(p) for p in REGION_SHEET_PREFIXES):
        return "region"
    if any(x in raw for x in ("2561", "2562", "2563", "2564", "2565", "2566", "2567", "2568")) and len(norm) < 20:
        if alias_to_province_id(norm, cw):
            return "province"
        return "region"
    if alias_to_province_id(norm, cw):
        return "province"
    if norm.startswith("รวม") and alias_to_province_id(norm[3:].strip(), cw):
        return "province"
    canon = _canonical_by_id(cw)
    for pid, th in sorted(canon.items(), key=lambda x: -len(x[1])):
        if th in norm and norm != th:
            return "sub_area"
    return "region"


def build_crosswalk_from_hii() -> pd.DataFrame:
    csv_path = RAW / "hii" / "spatial-rain-hii.csv"
    if not csv_path.exists():
        raise FileNotFoundError("Run python -m src.ingest.hii first")
    hii = pd.read_csv(csv_path)[["PROV_ID", "PROV_T"]].drop_duplicates().sort_values("PROV_ID")
    rows: list[dict[str, str]] = []
    for _, r in hii.iterrows():
        pid = str(int(r["PROV_ID"]))
        th = str(r["PROV_T"]).strip()
        rows.append(
            {
                "canonical_key": pid,
                "province_id": pid,
                "alias": th,
                "province_th": th,
                "province_en": EN_BY_ID.get(pid, ""),
                "region": REGION_BY_ID.get(pid, ""),
            }
        )
    for alias, pid in EXTRA_ALIASES.items():
        th = EN_BY_ID.get(pid, "")
        canon_th = next((x["province_th"] for x in rows if x["province_id"] == pid), alias)
        rows.append(
            {
                "canonical_key": pid,
                "province_id": pid,
                "alias": alias.strip(),
                "province_th": canon_th if isinstance(canon_th, str) else str(canon_th),
                "province_en": EN_BY_ID.get(pid, ""),
                "region": REGION_BY_ID.get(pid, ""),
            }
        )
    return pd.DataFrame(rows)


def write_crosswalk_if_needed() -> Path:
    path = CONFIG / "province_crosswalk.csv"
    df = build_crosswalk_from_hii()
    df.to_csv(path, index=False, encoding="utf-8")
    return path
