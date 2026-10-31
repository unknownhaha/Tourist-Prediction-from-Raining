from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config"
DATA = ROOT / "data"
RAW = DATA / "raw"
STAGING = DATA / "staging"
CORE = DATA / "core"
MANIFEST_DIR = DATA / "manifest"
MANIFEST_PATH = MANIFEST_DIR / "manifest_downloads.csv"
