import sys

from src.common.crosswalk_update import extend_from_mots
from src.staging.mots_registry import run_all


def main() -> int:
    path = run_all()
    added = extend_from_mots()
    if added:
        print(f"Crosswalk: added {added} MOTS aliases (map province_id manually if needed)")
    print(f"MOTS staging: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
