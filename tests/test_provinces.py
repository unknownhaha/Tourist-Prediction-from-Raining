import pandas as pd

from src.common.provinces import alias_to_province_id, classify_sheet, load_crosswalk, normalize_name


def test_normalize_name_strips_year_suffix():
    assert normalize_name("ภาคเหนือ2567") == "ภาคเหนือ"


def _first_match(alias: str, cw: pd.DataFrame) -> str | None:
    from src.common.provinces import normalize_name

    key = normalize_name(alias)
    if not key or key.lower() in ("nan", "none"):
        return None
    for col in ("alias", "province_th"):
        norms = cw[col].astype(str).map(normalize_name)
        hit = cw[norms == key]
        if not hit.empty:
            pid = hit.iloc[0]["province_id"]
            if pd.isna(pid) or str(pid).strip() == "":
                return None
            return str(int(float(pid)))
    return None


def test_alias_map_matches_full_scan():
    cw = load_crosswalk()
    names = pd.concat([cw["alias"], cw["province_th"]], ignore_index=True).dropna().astype(str)
    for name in names.unique():
        assert alias_to_province_id(name, cw) == _first_match(name, cw)


def test_alias_typos():
    cw = load_crosswalk()
    assert alias_to_province_id("กระบี", cw) == "81"
    assert alias_to_province_id("อยุธยา", cw) == "14"
    assert alias_to_province_id("ศรีษะเกษ", cw) == "33"
    assert alias_to_province_id("Thai", cw) is None


def test_classify_sheet_sub_area():
    cw = load_crosswalk()
    assert classify_sheet("เชียงใหม่", cw) == "province"
    assert classify_sheet("เกาะช้าง ตราด", cw) == "sub_area"
    assert classify_sheet("ภาคเหนือ", cw) == "region"
