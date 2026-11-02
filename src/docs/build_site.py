"""Build static role handbook explorer from docs/*.md and explorer.yaml."""

from __future__ import annotations

import json
import re
import shutil
import sys
from html import escape
from pathlib import Path

import markdown
import yaml

from src.common.paths import ROOT
from src.docs.national_series import load_national_series

DOCS = ROOT / "docs"
EXPLORER_YAML = DOCS / "explorer.yaml"
I18N_DIR = DOCS / "i18n"
SITE = ROOT / "site"
ASSETS_SRC = Path(__file__).resolve().parent / "assets"
LOCALES = ("en", "th")
GENERATED_NOTICE_CLASS = "locale-generated-notice"


def _strip_html(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", text).strip()


def _render_md(text: str) -> str:
    return markdown.markdown(
        text,
        extensions=["tables", "fenced_code", "nl2br"],
        output_format="html5",
    )


def _load_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _page_path(spec: dict, lang: str) -> Path:
    if lang == "th":
        rel = spec.get("path_th") or spec["path"]
    else:
        rel = spec["path"]
    return ROOT / rel if str(rel).startswith("docs/") else DOCS / Path(rel).name


def _build_page_html(spec: dict, lang: str, ui: dict) -> tuple[str, str]:
    path = _page_path(spec, lang)
    if not path.is_file():
        raise FileNotFoundError(f"Handbook page missing ({lang}): {path}")
    raw = path.read_text(encoding="utf-8")
    html = _render_md(raw)
    if lang == "th" and spec.get("generated") and not spec.get("path_th"):
        note = ui.get("generated_notice", "")
        html = f'<p class="{GENERATED_NOTICE_CLASS}"><em>{escape(note)}</em></p>\n' + html
    return html, _strip_html(html)


def _merge_stage(base: dict, overlay: dict | None) -> dict:
    out = {
        "id": base["id"],
        "title": base["title"],
        "command": base.get("command", ""),
        "when": base.get("when", ""),
        "inputs": base.get("inputs", ""),
        "outputs": base.get("outputs", ""),
        "notes": base.get("notes"),
        "page": base.get("page"),
        "optional": bool(base.get("optional")),
    }
    if overlay:
        for key in ("title", "when", "inputs", "outputs", "notes"):
            if overlay.get(key):
                out[key] = overlay[key]
    return out


def _merge_folder(base: dict, overlay: dict | None) -> dict:
    out = {
        "id": base["id"],
        "path": base["path"],
        "purpose": base["purpose"],
        "editable": base.get("editable", True),
    }
    if overlay and overlay.get("purpose"):
        out["purpose"] = overlay["purpose"]
    return out


def _build_locale(explorer: dict, lang: str, i18n: dict) -> dict:
    ui = i18n.get("ui", {})
    project_i18n = i18n.get("project", {})
    roles_i18n = i18n.get("roles", {})
    pages_i18n = i18n.get("pages", {})
    stages_i18n = i18n.get("stages", {})
    folders_i18n = i18n.get("folders", {})

    if lang == "en":
        project = {
            "name": project_i18n.get("name") or explorer["project"]["name"],
            "tagline": project_i18n.get("tagline") or explorer["project"].get("tagline", ""),
        }
    else:
        project = {
            "name": project_i18n.get("name") or explorer["project"]["name"],
            "tagline": project_i18n.get("tagline") or explorer["project"].get("tagline", ""),
        }

    roles: dict = {}
    for role_id, spec in explorer["roles"].items():
        ov = roles_i18n.get(role_id, {})
        roles[role_id] = {
            "title": ov.get("title") or spec["title"],
            "brief": ov.get("brief") or spec["brief"],
            "start_page": spec["start_page"],
        }

    pages: list[dict] = []
    for spec in explorer["pages"]:
        pid = spec["id"]
        ov = pages_i18n.get(pid, {})
        html, plain = _build_page_html(spec, lang, ui)
        pages.append(
            {
                "id": pid,
                "title": ov.get("title") or spec["title"],
                "html": html,
                "plain": plain,
                "generated": bool(spec.get("generated")),
            }
        )

    stages = [
        _merge_stage(s, stages_i18n.get(s["id"]))
        for s in explorer["stages"]
    ]
    folders = [
        _merge_folder(f, folders_i18n.get(f["id"]))
        for f in explorer["folders"]
    ]

    return {
        "project": project,
        "roles": roles,
        "pages": pages,
        "stages": stages,
        "folders": folders,
        "ui": ui,
    }


def _html_shell(payload: dict) -> str:
    data_json = json.dumps(payload, ensure_ascii=False)
    data_json = data_json.replace("</", "<\\/")
    en_name = escape(payload["locales"]["en"]["project"]["name"])
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{en_name} — Handbook</title>
  <link rel="stylesheet" href="assets/app.css">
  <script src="https://cdn.jsdelivr.net/npm/mermaid@11.4.0/dist/mermaid.min.js"></script>
</head>
<body>
  <a class="skip-link" href="#doc-body" id="skip-link">Skip to content</a>
  <header class="app-header">
    <h1 id="site-title">{en_name}</h1>
    <p class="tagline" id="site-tagline"></p>
    <div class="lang-tabs" id="lang-tabs" role="tablist" aria-label="Language"></div>
    <div class="role-tabs" id="role-tabs" role="tablist" aria-label="Reader role"></div>
    <div class="search-wrap">
      <label for="search-input" id="search-label">Search</label>
      <input type="search" id="search-input" autocomplete="off" placeholder="Handbook text…">
    </div>
  </header>
  <div class="layout">
    <aside class="sidebar" aria-label="Navigation">
      <section>
        <h2 id="nav-handbook-heading">Handbook</h2>
        <ul class="nav-pages" id="nav-pages"></ul>
      </section>
      <section>
        <h2 id="nav-pipeline-heading">Pipeline</h2>
        <ul class="nav-stages" id="nav-stages"></ul>
      </section>
      <section>
        <h2 id="nav-folders-heading">Folders</h2>
        <ul class="nav-folders" id="nav-folders"></ul>
      </section>
    </aside>
    <div class="main-column">
      <main class="content-pane">
        <div class="role-brief" id="role-brief"></div>
        <div id="search-results" hidden></div>
        <article class="doc-body" id="doc-body"></article>
      </main>
      <aside class="detail-pane" id="detail-pane" aria-label="Stage and folder details"></aside>
    </div>
  </div>
  <script type="application/json" id="explorer-data">{data_json}</script>
  <script src="assets/app.js"></script>
</body>
</html>
"""


def build() -> Path:
    explorer = _load_yaml(EXPLORER_YAML)
    locales_payload = {}
    for lang in LOCALES:
        i18n_path = I18N_DIR / f"{lang}.yaml"
        i18n = _load_yaml(i18n_path)
        locales_payload[lang] = _build_locale(explorer, lang, i18n)

    payload = {
        "defaultLocale": "en",
        "locales": locales_payload,
        "nationalSeries": load_national_series(),
    }

    SITE.mkdir(parents=True, exist_ok=True)
    assets = SITE / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    for name in ("app.css", "app.js"):
        shutil.copy2(ASSETS_SRC / name, assets / name)
    index = SITE / "index.html"
    index.write_text(_html_shell(payload), encoding="utf-8")
    return index


def main() -> int:
    path = build()
    print(f"Handbook site: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
