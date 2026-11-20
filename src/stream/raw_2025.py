"""Release a single 2025 province-month batch from the original MOTS/HII files."""
import hashlib
import json
from itertools import chain, islice
from pathlib import Path
from urllib.request import urlopen
from tempfile import NamedTemporaryFile

import pandas as pd
from openpyxl import load_workbook

from src.common.paths import ROOT, CONFIG
from src.common.provinces import load_crosswalk
from src.staging.mots_quarterly_province import _month_block
from src.stream.lineage import sha256_file
from src.stream.validate import StreamHalt


def source_files():
    return json.loads((CONFIG / "raw_stream_2025.json").read_text(encoding="utf-8"))


def _cached(item):
    path = ROOT / item["path"]
    if not path.exists():
        with urlopen(item["url"], timeout=30) as response:
            data = response.read()
        if hashlib.sha256(data).hexdigest() != item["sha256"]:
            raise ValueError(f"Source revision changed: {path.name}")
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=path.parent, delete=False) as temp:
            temp.write(data)
        Path(temp.name).replace(path)
    if sha256_file(path) != item["sha256"]:
        raise ValueError(f"Raw file checksum mismatch: {path.name}")
    return path


def _guests(path, number, sheets):
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        sheet_names = {name.strip(): name for name in workbook.sheetnames}
        for pid, title in sheets.items():
            sheet = workbook[sheet_names[title]]
            records = sheet.values
            header = list(islice(records, 6))
            label, months = _month_block(pd.DataFrame(header))
            if number not in months:
                raise ValueError(f"Missing month header in {path.name} / {sheet.title}")
            values = {}
            for row in chain(header, records):
                kind = str(row[label]).strip().lower()
                if kind == "grand total":
                    values = {"guest_arrivals_total": row[months[number]]}
                elif "guest_arrivals_total" in values and kind in ("thai", "foreigners"):
                    key = "guest_arrivals_thai" if kind == "thai" else "guest_arrivals_foreign"
                    values[key] = row[months[number]]
                if len(values) == 3:
                    break
            if len(values) != 3:
                raise ValueError(f"Incomplete guest grid in {path.name} / {sheet.title}")
            yield dict(province_id=pid, **values)
    finally:
        workbook.close()


def _collect_sources(loaded):
    """Split guest grids and rainfall frames. Manifest order does not matter."""
    guests = []
    rain_parts = []
    for kind, payload in loaded:
        if kind == "xlsx":
            guests.extend(payload)
        else:
            rain_parts.append(payload)
    if not rain_parts:
        raise ValueError("Manifest has no rainfall file")
    return guests, pd.concat(rain_parts, ignore_index=True)


def read_month(month):
    period = pd.Period(month, freq="M")
    if period.year != 2025:
        raise ValueError("Raw replay covers 2025 only")
    manifest = source_files()
    try:
        cw = load_crosswalk()
        loaded = []
        for item in manifest:
            path = _cached(item)
            if path.suffix.lower() == ".xlsx":
                loaded.append(("xlsx", list(_guests(path, period.month, item["sheets"]))))
            else:
                raw = pd.read_csv(path, dtype={"PROV_ID": str})
                part = raw.loc[(raw.YEAR == 2025) & (raw.MONTH == period.month),
                               ["PROV_ID", "MinRain", "AvgRain", "MaxRain"]].copy()
                part.columns = ["province_id", "rain_min_mm", "rain_avg_mm", "rain_max_mm"]
                loaded.append(("rain", part))
        guests, rain = _collect_sources(loaded)
        province = cw.dropna(subset=["province_id"]).drop_duplicates("province_id")
        panel = pd.DataFrame(guests).merge(rain, on="province_id", validate="one_to_one")
        panel = panel.merge(province[["province_id", "province_th", "province_en", "region"]],
                            on="province_id", validate="one_to_one")
        if len(panel) != 77:
            raise ValueError(f"Expected 77 matched provinces, found {len(panel)}")
        return panel.assign(year_month=month, year=2025, month=period.month, is_covid=False)
    except Exception as exc:
        raise StreamHalt(f"Raw ingest {month}: {exc}", year_month=month) from exc
