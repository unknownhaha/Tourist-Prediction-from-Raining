import pandas as pd

from src.common.crosswalk_update import unmapped_province_rows
from src.common.provinces import load_crosswalk


def test_sub_area_and_region_are_not_unmapped_provinces():
    cw = load_crosswalk()
    df = pd.DataFrame(
        [
            {"province_th_raw": "พัทยา ชลบุรี", "area_level": "sub_area", "province_id": None},
            {"province_th_raw": "เกาะช้าง ตราด", "area_level": "sub_area", "province_id": None},
            {"province_th_raw": "ภาคเหนือ", "area_level": "region", "province_id": None},
            {"province_th_raw": "เชียงใหม่", "area_level": "province", "province_id": "50"},
            {"province_th_raw": "ไม่มีจังหวัดนี้", "area_level": "province", "province_id": None},
        ]
    )
    out = unmapped_province_rows(df, cw)
    assert list(out["province_th_raw"]) == ["ไม่มีจังหวัดนี้"]
    assert int(out.iloc[0]["n_rows"]) == 1
