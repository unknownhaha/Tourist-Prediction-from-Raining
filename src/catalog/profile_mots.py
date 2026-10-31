"""Profile MOTS raw workbooks into docs/file_type_catalog.md."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

from src.common.paths import RAW


def profile_file(path: Path) -> dict:
    info = {"path": str(path.relative_to(RAW.parent.parent)), "sheets": [], "error": ""}
    try:
        xl = pd.ExcelFile(path)
        for sheet in xl.sheet_names[:15]:
            df = pd.read_excel(path, sheet_name=sheet, header=None, nrows=20)
            info["sheets"].append({"name": sheet, "shape": list(df.shape), "preview": df.iloc[:3].astype(str).values.tolist()})
    except Exception as e:
        info["error"] = str(e)[:200]
    return info


def main() -> int:
    from src.common.paths import ROOT

    docs = ROOT / "docs"
    docs.mkdir(exist_ok=True)
    mots = RAW / "mots"
    lines = ["# MOTS file type catalog", "", "Auto-generated workbook profiles.", ""]
    if not mots.exists():
        lines.append("_No MOTS raw files yet. Run the crawler first._")
    else:
        files = sorted(mots.rglob("*.xls*"))[:200]
        by_type: dict[str, list] = {}
        for f in files:
            prof = profile_file(f)
            key = "xlsx" if f.suffix.lower() == ".xlsx" else "xls"
            by_type.setdefault(key, []).append(prof)
        for ftype, items in sorted(by_type.items()):
            lines.append(f"## Type: {ftype}")
            lines.append(f"- Files sampled: {len(items)}")
            lines.append("- Typical grain: province × month when header contains จังหวัด and Thai month columns")
            lines.append("- Metrics: visitors (heuristic); refine after manual review")
            lines.append("")
            for item in items[:5]:
                lines.append(f"### `{item['path']}`")
                if item["error"]:
                    lines.append(f"- Error: {item['error']}")
                for sh in item["sheets"][:3]:
                    lines.append(f"- Sheet `{sh['name']}` shape {sh['shape']}")
                lines.append("")
    out = docs / "file_type_catalog.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
