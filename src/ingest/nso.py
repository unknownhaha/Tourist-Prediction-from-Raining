"""Download NSO MD accommodation survey RAR archives."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pandas as pd
import rarfile
import requests

from src.common.calendar import be_to_ce
from src.common.config_loader import load_sources
from src.common.manifest import append_row, new_run_id, sha256_file
from src.common.paths import RAW, STAGING

NSO_RAW = RAW / "nso"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def _rar_links(html: str) -> list[str]:
    return re.findall(
        r'href="(https://www\.nso\.go\.th/nsoweb/storage/[^"]+\.rar)"', html
    )


def fetch_round(year_be: int, subcode: str, run_id: str) -> list[Path]:
    cfg = load_sources()["nso"]
    url = f"{cfg['get_survey_file_url']}?code={cfg['survey_code']}&subcode={subcode}&type_menu={cfg['type_menu']}"
    resp = requests.get(url, headers=UA, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    links = _rar_links(data.get("html", ""))
    dest_dir = NSO_RAW / f"MD_{year_be}"
    dest_dir.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    for link in links:
        r = requests.get(link, headers=UA, timeout=300)
        name = link.split("/")[-1]
        dest = dest_dir / name
        dest.write_bytes(r.content)
        digest = sha256_file(dest)
        append_row(
            {
                "run_id": run_id,
                "source": "nso",
                "year_be": year_be,
                "year_ce": be_to_ce(year_be),
                "file_url": link,
                "local_path": str(dest.relative_to(RAW.parent.parent)),
                "file_type": "rar",
                "sha256": digest,
                "size_bytes": dest.stat().st_size,
                "http_status": r.status_code,
                "status": "ok",
            }
        )
        saved.append(dest)
    return saved


def _seven_zip() -> Path | None:
    for candidate in (
        shutil.which("7z"),
        Path(r"C:\Program Files\7-Zip\7z.exe"),
        Path(r"C:\Program Files (x86)\7-Zip\7z.exe"),
    ):
        if candidate and Path(candidate).exists():
            return Path(candidate)
    return None


def extract_rar(rar_path: Path) -> Path:
    extract_dir = rar_path.parent / rar_path.stem
    extract_dir.mkdir(exist_ok=True)
    seven = _seven_zip()
    if seven:
        subprocess.run(
            [str(seven), "x", "-y", f"-o{extract_dir}", str(rar_path)],
            check=True,
            capture_output=True,
        )
        return extract_dir
    unrar = shutil.which("unrar") or shutil.which("UnRAR")
    if unrar:
        rarfile.UNRAR_TOOL = unrar
    try:
        with rarfile.RarFile(rar_path) as rf:
            rf.extractall(path=extract_dir)
    except (rarfile.Error, rarfile.RarCannotExec):
        if zipfile.is_zipfile(rar_path):
            with zipfile.ZipFile(rar_path) as zf:
                zf.extractall(extract_dir)
        else:
            raise RuntimeError(
                "RAR extraction requires 7-Zip (7z.exe) or UnRAR on PATH. "
                "Install from https://www.7-zip.org/"
            ) from None
    return extract_dir


def stage_metadata(run_id: str) -> Path:
    rows = []
    for round_cfg in load_sources()["nso"]["rounds"]:
        year_be = round_cfg["year_be"]
        folder = NSO_RAW / f"MD_{year_be}"
        if not folder.exists():
            rows.append(
                {
                    "source": "nso",
                    "survey_year_be": year_be,
                    "survey_year_ce": be_to_ce(year_be),
                    "status": "missing_folder",
                    "file_path": "",
                }
            )
            continue
        for rar in folder.glob("*.rar"):
            try:
                extract_dir = extract_rar(rar)
            except RuntimeError as e:
                rows.append(
                    {
                        "source": "nso",
                        "survey_year_be": year_be,
                        "survey_year_ce": be_to_ce(year_be),
                        "status": "extract_failed",
                        "file_path": str(rar.relative_to(RAW.parent.parent)),
                        "error": str(e),
                    }
                )
                append_row(
                    {
                        "run_id": run_id,
                        "source": "nso",
                        "year_be": year_be,
                        "year_ce": be_to_ce(year_be),
                        "local_path": str(rar.relative_to(RAW.parent.parent)),
                        "status": "extract_failed",
                        "error_message": str(e)[:500],
                    }
                )
                continue
            for xlsx in extract_dir.rglob("*.xls*"):
                rows.append(
                    {
                        "source": "nso",
                        "survey_year_be": year_be,
                        "survey_year_ce": be_to_ce(year_be),
                        "status": "extracted",
                        "file_path": str(xlsx.relative_to(RAW.parent.parent)),
                    }
                )
    out = STAGING / "nso"
    out.mkdir(parents=True, exist_ok=True)
    path = out / "fact_accommodation_nso_inventory.parquet"
    pd.DataFrame(rows).to_parquet(path, index=False)
    return path


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="NSO MD accommodation ingest")
    parser.add_argument("--stage-only", action="store_true", help="Extract/stage existing RARs only")
    args = parser.parse_args(argv)

    run_id = new_run_id()
    if not args.stage_only:
        for round_cfg in load_sources()["nso"]["rounds"]:
            fetch_round(round_cfg["year_be"], round_cfg["subcode"], run_id)
    stage_metadata(run_id)
    print(f"NSO inventory: {STAGING / 'nso' / 'fact_accommodation_nso_inventory.parquet'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
