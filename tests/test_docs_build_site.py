"""Tests for docs/explorer.yaml and handbook site builder."""

from __future__ import annotations

from pathlib import Path

import yaml

from src.common.paths import ROOT
from src.docs.build_site import EXPLORER_YAML, build

DOCS = ROOT / "docs"


def _explorer() -> dict:
    with EXPLORER_YAML.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_explorer_page_paths_exist():
    exp = _explorer()
    for spec in exp["pages"]:
        for key in ("path", "path_th"):
            rel = spec.get(key)
            if not rel or spec.get("generated") and key == "path_th":
                continue
            path = ROOT / rel if rel.startswith("docs/") else DOCS / Path(rel).name
            assert path.is_file(), f"missing page for {spec['id']} ({key}): {path}"


def test_explorer_stages_have_command_and_outputs():
    exp = _explorer()
    for stage in exp["stages"]:
        assert stage.get("command"), f"stage {stage['id']} missing command"
        assert stage.get("outputs"), f"stage {stage['id']} missing outputs"


def test_build_site_includes_roles_and_commands():
    index = build()
    html = index.read_text(encoding="utf-8")
    exp = _explorer()
    for role_id in exp["roles"]:
        assert role_id in html, f"role {role_id} not embedded in site"
    for stage in exp["stages"]:
        assert stage["command"] in html, f"command for {stage['id']} not in site"
    assert '"locales"' in html
    assert '"th"' in html
    assert "docs/th/index.md" not in html
    assert "ดัชนีคู่มือ" in html or "ทำนายนักท่องเที่ยว" in html
