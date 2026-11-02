import pandas as pd

from src.common.provinces import alias_to_province_id, classify_sheet, load_crosswalk, normalize_name


def test_normalize_name_strips_year_suffix():
    assert normalize_name("ภาคเหนือ2567") == "ภาคเหนือ"


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
