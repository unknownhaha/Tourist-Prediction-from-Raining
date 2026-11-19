"""Clean MOTS 'สถิตินักท่องเที่ยวภายในประเทศ (จำแนกตามภูมิภาคและจังหวัด)' workbooks (2019+).

Input : data/raw/mots_domestic_province/<year>/*.xlsx  (+ index.csv from the fetch script)
       data/staging/hii/fact_rainfall.parquet (HII rainfall)
Output: data/clean/mots_domestic_province/
  - province_quarter.parquet / .csv    one row per province x quarter: tourism metrics + rainfall
  - province_month.parquet / .csv      one row per province x month: guest arrivals + rainfall
  - tourism_sheets_long.parquet / .csv every parsed sheet value (sub-areas tagged with parent province)
  - subarea_rollup_check.csv           sum of sub-areas vs province total
  - qa_report.md                       validation results

Sub-area sheets (Pattaya, Bang Saen, Hua Hin, ...) are folded into their parent province; the
province tables contain provinces only. Tests: tests/test_province_level_merge.py

Run from the project root:  python scripts/clean_domestic_province.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.common.config_loader import load_sources  # noqa: E402
from src.common.provinces import alias_to_province_id, classify_sheet, load_crosswalk, normalize_name  # noqa: E402

RAW_DIR = ROOT / "data" / "raw" / "mots_domestic_province"
OUT_DIR = ROOT / "data" / "clean" / "mots_domestic_province"

QUARTER_HEADER = {"january-march": 1, "april-june": 2, "july-september": 3, "october-december": 4}
QUARTER_MONTHS = {1: ("jan", "feb", "mar"), 2: ("apr", "may", "jun"), 3: ("jul", "aug", "sep"), 4: ("oct", "nov", "dec")}
MONTH_NUM = {m: i + 1 for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split())}

# metric name, unit for each (section, group) in the left summary block
METRICS = {
    (None, "visitor"): ("visitors", "persons"),
    (None, "tourist"): ("tourists", "persons"),
    (None, "excursionist"): ("excursionists", "persons"),
    ("los", None): ("avg_length_of_stay_days", "days"),
    ("exp", "visitor"): ("avg_expenditure_visitor", "baht_per_person_day"),
    ("exp", "tourist"): ("avg_expenditure_tourist", "baht_per_person_day"),
    ("exp", "excursionist"): ("avg_expenditure_excursionist", "baht_per_person_day"),
    ("rev", "visitor"): ("revenue_visitor", "million_baht"),
    ("rev", "tourist"): ("revenue_tourist", "million_baht"),
    ("rev", "excursionist"): ("revenue_excursionist", "million_baht"),
    ("acc", "rooms"): ("rooms", "rooms"),
    ("acc", "occupancy"): ("occupancy_rate_pct", "percent"),
    ("acc", "guests"): ("guest_arrivals", "persons"),
}


def norm_label(v) -> str:
    return re.sub(r"\s+", " ", str(v)).strip().lower() if pd.notna(v) else ""


def to_num(v):
    if pd.isna(v):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(",", "").strip()
    if s in ("", "-", "–", "n/a", "na"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def quarter_blocks(df: pd.DataFrame) -> list[tuple[int, int, int]]:
    """Return (label_col, quarter, year_ce) for each left-summary block."""
    out = []
    # Row 1 holds 'January-March' above the current-year value column; labels sit one column left.
    for j in range(1, df.shape[1]):
        q = QUARTER_HEADER.get(norm_label(df.iat[1, j]).replace(" ", ""))
        if q is None:
            continue
        year = to_num(df.iat[2, j])
        if year and 2000 < year < 2100:
            out.append((j - 1, q, int(year)))
    return out


def parse_summary(df: pd.DataFrame, col: int) -> list[tuple[str, str, str, float, float | None]]:
    """Walk the label column of one block -> (metric, tourist_type, unit, value, prior_year_value)."""
    rows = []
    section, group = None, None
    for r in range(3, min(len(df), 45)):
        lab = norm_label(df.iat[r, col])
        if not lab:
            continue
        if lab.startswith("remark"):
            break
        cur, prev = to_num(df.iat[r, col + 1]), to_num(df.iat[r, col + 2])
        ttype = "total"
        if "length of stay" in lab:
            section, group = "los", None
        elif "expenditure" in lab:
            section, group = "exp", None
            continue
        elif lab.startswith("revenue"):
            section, group = "rev", None
            continue
        elif lab.startswith("accommodation"):
            section, group = "acc", None
            continue
        elif lab in ("visitor", "tourist", "excursionist"):
            group = lab
        elif lab == "rooms":
            group = "rooms"
        elif lab.startswith("occupancy"):
            group = "occupancy"
        elif "guest arrivals" in lab:
            group = "guests"
        elif lab == "thai":
            ttype = "thai"
        elif lab in ("foreigners", "foreigner"):
            ttype = "foreign"
        else:
            continue
        key = (section, group)
        if key not in METRICS or cur is None:
            continue
        metric, unit = METRICS[key]
        rows.append((metric, ttype, unit, cur, prev))
    return rows


def parse_monthly_guests(df: pd.DataFrame, q: int) -> list[tuple[int, str, float]]:
    """Find the nationality grid (Jan/Feb/Mar headers) for quarter q -> (month, tourist_type, value)."""
    months = QUARTER_MONTHS[q]
    out = []
    for j in range(df.shape[1] - 2):
        if [norm_label(df.iat[2, j + k]) for k in range(3)] != list(months):
            continue
        label_col = j - 1
        started = False  # the grid also opens with a 'Thai' row; read only the Grand Total/Thai/Foreigners footer
        for r in range(3, min(len(df), 60)):
            lab = norm_label(df.iat[r, label_col])
            ttype = {"grand total": "total", "thai": "thai", "foreigners": "foreign"}.get(lab)
            started = started or ttype == "total"
            if ttype is None or not started:
                continue
            for k, m in enumerate(months):
                v = to_num(df.iat[r, j + k])
                if v is not None:
                    out.append((MONTH_NUM[m], ttype, v))
            if ttype == "foreign":
                break
        break
    return out


# ---------------------------------------------------------------------------------------------
# Sheet roles
# ---------------------------------------------------------------------------------------------
# Sub-area sheets whose names do not contain their province name. Parent verified against the
# English header in row 0 of each sheet ("Internal tourism in Pai MaeHongSon", ...).
SUBAREA_PARENT = {
    "ในเมือง": "30",                 # Mueang Nakhon Ratchasima
    "วังน้ำเขียว": "30",             # Wang Nam Khiao, Nakhon Ratchasima
    "ปากช่อง นครราชสีมา": "30",
    "เมือง แม่ฮองสอน": "58",
    "ปาย": "58",
    "1.1เมือง ประจวบ": "77",
    "1.2หัวหิน ประจวบฯ": "77",
    "1.1เมือง ประจวบคีรีขันธ์": "77",
    "1.2หัวหิน ประจวบคีรีขันธ์": "77",
    "1.1อำเภอเมืองและอื่นๆ": "76",
    "1.1อำเภอเมืองและอื่นๆจ.เพชรบุรี": "76",
    "1.2 อำเภอชะอำ": "76",
}
# Sheets that are themselves a sum of other sub-area sheets: kept for audit, never added again.
SUBTOTAL_SHEETS = {"เมือง+วังน้ำเขียว": "30", "เมือง+วังน้ำเขียว1": "30"}
# Header keywords that must appear in row 0 of a mapped sub-area sheet (guards against a wrong map).
PARENT_HEADER_HINT = {
    "30": ("nakhon",), "58": ("pai", "maehongson"), "77": ("prachuap", "hua hin"),
    "76": ("phetchaburi", "petchaburi", "cha am"), "20": ("chon",), "23": ("trat",), "84": ("surat",),
}

ADDITIVE = ["visitors", "tourists", "excursionists", "guest_arrivals", "revenue_visitor", "rooms"]
# non-additive metric -> additive metric used as weight when a province has to be rebuilt from parts
WEIGHTED = {
    "avg_expenditure_visitor": "visitors", "avg_expenditure_tourist": "tourists",
    "avg_expenditure_excursionist": "excursionists", "avg_length_of_stay_days": "tourists",
    "occupancy_rate_pct": "rooms",
}


def resolve_sheet(name: str, cw: pd.DataFrame, canon: dict[str, str]) -> tuple[str, str | None]:
    """Return (role, province_id). role: province_total | sub_area | subtotal | ignore."""
    if name in SUBTOTAL_SHEETS:
        return "subtotal", SUBTOTAL_SHEETS[name]
    if name in SUBAREA_PARENT:
        return "sub_area", SUBAREA_PARENT[name]
    level = classify_sheet(name, cw)
    if level == "province":
        pid = alias_to_province_id(normalize_name(name), cw) or alias_to_province_id(name, cw)
        if pid:
            return "province_total", pid
    if level == "sub_area":
        norm = normalize_name(name)
        for pid, th in sorted(canon.items(), key=lambda x: -len(x[1])):
            if th in norm:
                return "sub_area", pid
    return "ignore", None


def header_text(df: pd.DataFrame) -> str:
    return " ".join(str(v) for v in df.iloc[0].dropna().tolist()).lower()


# ---------------------------------------------------------------------------------------------
# Rainfall: collapse to one row per province x month (same rule as tourism: province level only)
# ---------------------------------------------------------------------------------------------
def province_rainfall(cw: pd.DataFrame, valid_ids: set[str]) -> tuple[pd.DataFrame, dict]:
    raw = pd.read_parquet(ROOT / "data" / "staging" / "hii" / "fact_rainfall.parquet")
    raw = raw.assign(province_id=raw["province_id"].astype(str))
    # map any non-province label (district / alias) to its province through the crosswalk
    bad = ~raw["province_id"].isin(valid_ids)
    if bad.any():
        raw.loc[bad, "province_id"] = raw.loc[bad, "province_th"].map(
            lambda n: alias_to_province_id(n, cw) or alias_to_province_id(normalize_name(n), cw))
    stats = {"rain_input_rows": len(raw), "rain_unmapped_rows": int(raw["province_id"].isna().sum())}
    raw = raw.dropna(subset=["province_id"])
    m = (raw.groupby(["province_id", "year_ce", "month", "year_month"], as_index=False)
         .agg(rain_avg_mm=("avg_rain_mm", "mean"), rain_min_mm=("min_rain_mm", "min"),
              rain_max_mm=("max_rain_mm", "max"), rain_source_rows=("avg_rain_mm", "size")))
    stats["rain_rows_collapsed"] = int((m["rain_source_rows"] > 1).sum())
    return m, stats


def rain_quarterly(m: pd.DataFrame) -> pd.DataFrame:
    m = m.assign(quarter=(m["month"] - 1) // 3 + 1)
    return (m.groupby(["province_id", "year_ce", "quarter"], as_index=False)
            .agg(rain_sum_mm=("rain_avg_mm", "sum"), rain_mean_monthly_mm=("rain_avg_mm", "mean"),
                 rain_min_mm=("rain_min_mm", "min"), rain_max_mm=("rain_max_mm", "max"),
                 rain_months=("rain_avg_mm", "size")))


# ---------------------------------------------------------------------------------------------
def rollup(parts: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    """Combine sub-area rows into one province row: sum additive metrics, weight the rest."""
    add = (parts[parts.metric.isin(ADDITIVE)]
           .groupby(keys + ["metric", "tourist_type"], as_index=False)["value"].sum())
    out = [add]
    for metric, wm in WEIGHTED.items():
        p = parts[parts.metric == metric]
        if p.empty:
            continue
        wt = parts[parts.metric == wm][["sheet", *keys, "tourist_type", "value"]].rename(columns={"value": "w"})
        if wm == "rooms":  # rooms are reported only as total
            p = p.merge(wt.drop(columns="tourist_type"), on=["sheet", *keys])
        else:
            p = p.merge(wt, on=["sheet", *keys, "tourist_type"])
        g = p.assign(vw=p.value * p.w).groupby(keys + ["tourist_type"], as_index=False)[["vw", "w"]].sum()
        g["value"] = g.vw / g.w.where(g.w != 0)
        out.append(g.assign(metric=metric)[keys + ["metric", "tourist_type", "value"]])
    return pd.concat(out, ignore_index=True)


def province_level(df: pd.DataFrame, keys: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """One value per province x period x metric: province sheet if present, else roll-up of sub-areas."""
    tot = df[df.role == "province_total"].copy()
    tot["_r"] = (~tot.sheet.str.replace(r"^\d+\.\s*", "", regex=True).str.startswith("รวม")).astype(int)
    tot = tot.sort_values("_r").drop_duplicates(keys + ["metric", "tourist_type"]).drop(columns="_r")
    parts = df[df.role == "sub_area"]
    rolled = rollup(parts, keys)
    chk = rolled[rolled.metric.isin(ADDITIVE)].merge(
        tot[keys + ["metric", "tourist_type", "value"]], on=keys + ["metric", "tourist_type"],
        how="left", suffixes=("_subareas", "_province"))
    missing = rolled.merge(tot[keys].drop_duplicates(), on=keys, how="left", indicator=True)
    missing = missing[missing._merge == "left_only"].drop(columns="_merge")
    final = pd.concat([tot[keys + ["metric", "tourist_type", "value"]].assign(method="province_sheet"),
                       missing.assign(method="rolled_up_from_sub_areas")], ignore_index=True)
    return final, chk


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cw = load_crosswalk()
    dim = pd.read_parquet(ROOT / "data" / "core" / "dim_province.parquet")
    dim = dim.assign(province_id=dim.province_id.astype(str))
    valid_ids = set(dim.province_id)
    canon = dict(zip(dim.province_id, dim.province_th))

    index = pd.read_csv(RAW_DIR / "index.csv", encoding="utf-8-sig")
    index["file"] = index["local_path"].map(lambda p: Path(p).name)

    qrows, mrows, notes, ignored = [], [], [], set()
    for _, f in index.iterrows():
        path = RAW_DIR / str(f["year_ce"]) / f["file"]
        if "รายเดือน" in f["title"]:
            notes.append(f"{f['file']}: monthly (R) report – different layout, not part of the Q1-Q4 series")
            continue
        xl = pd.ExcelFile(path)
        for sheet in xl.sheet_names:
            name = str(sheet).strip()
            role, pid = resolve_sheet(name, cw, canon)
            df = pd.read_excel(xl, sheet_name=sheet, header=None)
            blocks = quarter_blocks(df) if df.shape[0] >= 40 else []
            if role == "ignore" or not blocks:
                if blocks:
                    ignored.add(name)
                continue
            if role in ("sub_area", "subtotal") and pid in PARENT_HEADER_HINT:
                if not any(h in header_text(df) for h in PARENT_HEADER_HINT[pid]):
                    notes.append(f"header of '{name}' ({f['year_ce']}) does not name {canon[pid]}: "
                                 f"{header_text(df)[:70]}")
            base = {"province_id": pid, "role": role, "sheet": name,
                    "source_title": f["title"], "source_file": f["file"]}
            for col, q, year in blocks:
                for metric, ttype, unit, cur, prev in parse_summary(df, col):
                    qrows.append({**base, "year_ce": year, "quarter": q, "metric": metric,
                                  "tourist_type": ttype, "unit": unit, "value": cur, "prior_year_value": prev})
                for month, ttype, v in parse_monthly_guests(df, q):
                    mrows.append({**base, "year_ce": year, "quarter": q, "month": month,
                                  "metric": "guest_arrivals", "tourist_type": ttype, "value": v})

    ql, ml = pd.DataFrame(qrows), pd.DataFrame(mrows)
    n0q, n0m = len(ql), len(ml)
    # re-uploaded workbooks (e.g. ภาคเหนือ 2566 twice): keep the latest upload of each sheet
    ql = ql.sort_values("source_file").drop_duplicates(
        ["sheet", "year_ce", "quarter", "metric", "tourist_type"], keep="last")
    ml = ml.sort_values("source_file").drop_duplicates(["sheet", "year_ce", "month", "tourist_type"], keep="last")

    qkeys, mkeys = ["province_id", "year_ce", "quarter"], ["province_id", "year_ce", "quarter", "month"]
    pq, chk_q = province_level(ql, qkeys)
    pm, _ = province_level(ml, mkeys)

    info = dim.set_index("province_id")
    id_cols = ["province_id", "province_th", "province_en", "region"]

    def add_names(d: pd.DataFrame) -> pd.DataFrame:
        return d.assign(province_th=d.province_id.map(info.province_th),
                        province_en=d.province_id.map(info.province_en),
                        region=d.province_id.map(info.region))

    wide = pq.pivot_table(index=qkeys, columns=["metric", "tourist_type"], values="value", aggfunc="first")
    wide.columns = [f"{m}_{t}" for m, t in wide.columns]
    wide = wide.reset_index()
    month_w = pm.pivot_table(index=mkeys, columns="tourist_type", values="value", aggfunc="first")
    month_w.columns = [f"guest_arrivals_{c}" for c in month_w.columns]
    month_w = month_w.reset_index()
    month_w["year_month"] = month_w.year_ce.astype(str) + "-" + month_w.month.map("{:02d}".format)

    rain_m, rain_stats = province_rainfall(cw, valid_ids)
    rain_q = rain_quarterly(rain_m)
    merged_q = add_names(wide.merge(rain_q, on=qkeys, how="left", validate="one_to_one"))
    merged_m = add_names(month_w.merge(rain_m.drop(columns=["year_ce", "month"]), on=["province_id", "year_month"],
                                       how="left", validate="one_to_one"))
    lead = id_cols + ["year_ce", "quarter"]
    merged_q = merged_q[lead + [c for c in merged_q.columns if c not in lead]].sort_values(lead[:1] + lead[4:])
    leadm = id_cols + ["year_month", "year_ce", "quarter", "month"]
    merged_m = merged_m[leadm + [c for c in merged_m.columns if c not in leadm]].sort_values(["province_id", "year_month"])
    for d in (merged_q, merged_m):
        d["province_id"] = d["province_id"].astype(int)

    # ---- COVID window from config/sources.yaml (same window as is_covid in data/core/province_month)
    cov = load_sources()["covid_window"]
    merged_m["is_covid"] = merged_m.year_month.between(cov["start"], cov["end"])
    # a quarter is COVID if any of its months is inside the window
    q_first = merged_q.year_ce.astype(str) + "-" + ((merged_q.quarter - 1) * 3 + 1).map("{:02d}".format)
    q_last = merged_q.year_ce.astype(str) + "-" + (merged_q.quarter * 3).map("{:02d}".format)
    merged_q["is_covid"] = (q_last >= cov["start"]) & (q_first <= cov["end"])

    long_out = add_names(ql.rename(columns={"role": "sheet_role"}))
    long_out["year_quarter"] = long_out.year_ce.astype(str) + "-Q" + long_out.quarter.astype(str)
    long_cols = ["province_id", "province_th", "province_en", "region", "sheet_role", "sheet", "year_ce", "quarter",
                 "year_quarter", "metric", "tourist_type", "unit", "value", "prior_year_value",
                 "source_title", "source_file"]
    long_out = long_out[long_cols].sort_values(
        ["province_id", "sheet_role", "sheet", "year_ce", "quarter", "metric", "tourist_type"])

    for old in ("quarterly_wide.csv", "monthly_guest_arrivals.csv", "quarterly_long.csv", "quarterly_long.parquet",
                "province_quarter_no_covid.csv", "province_quarter_no_covid.parquet",
                "province_month_no_covid.csv", "province_month_no_covid.parquet"):
        (OUT_DIR / old).unlink(missing_ok=True)
    long_out.to_parquet(OUT_DIR / "tourism_sheets_long.parquet", index=False)
    long_out.to_csv(OUT_DIR / "tourism_sheets_long.csv", index=False, encoding="utf-8-sig")
    merged_q.to_parquet(OUT_DIR / "province_quarter.parquet", index=False)
    merged_q.to_csv(OUT_DIR / "province_quarter.csv", index=False, encoding="utf-8-sig")
    merged_m.to_parquet(OUT_DIR / "province_month.parquet", index=False)
    merged_m.to_csv(OUT_DIR / "province_month.csv", index=False, encoding="utf-8-sig")
    chk_q = add_names(chk_q)
    chk_q["diff"] = chk_q.value_subareas - chk_q.value_province
    chk_q.to_csv(OUT_DIR / "subarea_rollup_check.csv", index=False, encoding="utf-8-sig")

    qa = ["# QA report – province-level tourism (MOTS) + rainfall (HII)", ""]
    qa.append(f"- Duplicate rows dropped from re-uploaded workbooks: quarterly {n0q - len(ql):,}, "
              f"monthly {n0m - len(ml):,}")
    qa.append(f"- Rainfall: {rain_stats['rain_input_rows']:,} input rows, {rain_stats['rain_unmapped_rows']} unmapped, "
              f"{rain_stats['rain_rows_collapsed']} province-months built from more than one row")
    qa.append(f"- province_quarter: {len(merged_q):,} rows, {merged_q.province_id.nunique()} provinces, "
              f"{merged_q[['year_ce', 'quarter']].drop_duplicates().shape[0]} quarters")
    qa.append(f"- province_month: {len(merged_m):,} rows, {merged_m.province_id.nunique()} provinces, "
              f"{merged_m.year_month.nunique()} months")
    qa.append(f"- is_covid ({cov['start']} … {cov['end']}): quarterly {int(merged_q.is_covid.sum()):,} True / "
              f"{int((~merged_q.is_covid).sum()):,} False; monthly {int(merged_m.is_covid.sum()):,} True / "
              f"{int((~merged_m.is_covid).sum()):,} False")
    qa.append(f"- Province values taken from the province sheet: {(pq.method == 'province_sheet').sum():,}; "
              f"rebuilt from sub-areas: {(pq.method != 'province_sheet').sum():,}")
    qa += ["", "## Sub-areas folded into their province", ""]
    sub = (long_out[long_out.sheet_role.isin(["sub_area", "subtotal"])]
           .groupby(["province_th", "sheet_role"]).sheet.agg(lambda s: ", ".join(sorted(set(s)))))
    qa += [f"- {p} ({r}): {s}" for (p, r), s in sub.items()]
    qa += ["", "`subtotal` sheets are sums of other sub-areas and are not added again.", "",
           "## Roll-up check: sum of sub-areas vs province sheet (additive metrics, all quarters)", "",
           "| province | metric | rows | max abs diff | max diff % |", "|---|---|---:|---:|---:|"]
    for (p, met), g in chk_q.groupby(["province_th", "metric"]):
        rel = (g["diff"].abs() / g.value_province.abs().where(g.value_province != 0)).max()
        qa.append(f"| {p} | {met} | {len(g)} | {g['diff'].abs().max():,.2f} | {0 if pd.isna(rel) else rel * 100:.3f} |")
    qa += ["", "## Ignored sheets that contain tourism tables (region / country totals)", "",
           ", ".join(sorted(ignored)), "", "## Notes", ""] + [f"- {n}" for n in (notes or ["none"])]
    (OUT_DIR / "qa_report.md").write_text("\n".join(qa) + "\n", encoding="utf-8")
    print("\n".join(qa))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
