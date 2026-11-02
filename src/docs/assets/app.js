(function () {
  "use strict";

  var dataEl = document.getElementById("explorer-data");
  if (!dataEl) return;

  var DATA = JSON.parse(dataEl.textContent);
  var defaultLocale = DATA.defaultLocale || "en";

  var state = {
    lang: defaultLocale,
    role: "operator",
    pageId: "index",
    stageId: null,
    folderId: null,
    searchQuery: "",
  };

  var els = {
    skipLink: document.getElementById("skip-link"),
    siteTitle: document.getElementById("site-title"),
    siteTagline: document.getElementById("site-tagline"),
    langTabs: document.getElementById("lang-tabs"),
    roleTabs: document.getElementById("role-tabs"),
    searchInput: document.getElementById("search-input"),
    searchLabel: document.getElementById("search-label"),
    navHandbook: document.getElementById("nav-handbook-heading"),
    navPipeline: document.getElementById("nav-pipeline-heading"),
    navFolders: document.getElementById("nav-folders-heading"),
    roleBrief: document.getElementById("role-brief"),
    docBody: document.getElementById("doc-body"),
    detailPane: document.getElementById("detail-pane"),
    navPages: document.getElementById("nav-pages"),
    navStages: document.getElementById("nav-stages"),
    navFoldersList: document.getElementById("nav-folders"),
    searchResults: document.getElementById("search-results"),
  };

  function bundle() {
    return DATA.locales[state.lang] || DATA.locales[defaultLocale];
  }

  function ui() {
    return bundle().ui || {};
  }

  function pages() {
    return bundle().pages;
  }

  function roles() {
    return bundle().roles;
  }

  function stages() {
    return bundle().stages;
  }

  function folders() {
    return bundle().folders;
  }

  function project() {
    return bundle().project;
  }

  function pageById(id) {
    var list = pages();
    for (var i = 0; i < list.length; i++) {
      if (list[i].id === id) return list[i];
    }
    return list[0];
  }

  function stageById(id) {
    var list = stages();
    for (var i = 0; i < list.length; i++) {
      if (list[i].id === id) return list[i];
    }
    return null;
  }

  function folderById(id) {
    var list = folders();
    for (var i = 0; i < list.length; i++) {
      if (list[i].id === id) return list[i];
    }
    return null;
  }

  function parseHash() {
    var hash = window.location.hash.replace(/^#/, "");
    if (!hash) {
      state.stageId = null;
      state.folderId = null;
      return;
    }
    var params = {};
    hash.split("&").forEach(function (part) {
      var kv = part.split("=");
      if (kv[0]) params[decodeURIComponent(kv[0])] = decodeURIComponent(kv[1] || "");
    });
    if (params.lang && DATA.locales[params.lang]) state.lang = params.lang;
    if (params.role && roles()[params.role]) state.role = params.role;
    if (params.page && pageById(params.page)) state.pageId = params.page;
    state.stageId = params.stage || null;
    state.folderId = params.folder || null;
  }

  function writeHash() {
    var parts = [
      "lang=" + encodeURIComponent(state.lang),
      "role=" + encodeURIComponent(state.role),
      "page=" + encodeURIComponent(state.pageId),
    ];
    if (state.stageId) parts.push("stage=" + encodeURIComponent(state.stageId));
    if (state.folderId) parts.push("folder=" + encodeURIComponent(state.folderId));
    var next = "#" + parts.join("&");
    if (window.location.hash !== next) window.location.hash = next;
  }

  function applyChrome() {
    var u = ui();
    var p = project();
    document.documentElement.lang = state.lang === "th" ? "th" : "en";
    document.title = p.name + " — " + (u.doc_title_suffix || "Handbook");
    if (els.skipLink) els.skipLink.textContent = u.skip_link || "Skip to content";
    if (els.siteTitle) els.siteTitle.textContent = p.name;
    if (els.siteTagline) els.siteTagline.textContent = p.tagline || "";
    if (els.searchLabel) els.searchLabel.textContent = u.search_label || "Search";
    if (els.searchInput) els.searchInput.placeholder = u.search_placeholder || "";
    if (els.navHandbook) els.navHandbook.textContent = u.handbook || "Handbook";
    if (els.navPipeline) els.navPipeline.textContent = u.pipeline || "Pipeline";
    if (els.navFolders) els.navFolders.textContent = u.folders || "Folders";
  }

  function renderLangTabs() {
    if (!els.langTabs) return;
    els.langTabs.innerHTML = "";
    var label = document.createElement("span");
    label.className = "lang-label";
    label.textContent = (ui().lang_label || "Language") + ":";
    els.langTabs.appendChild(label);
    ["en", "th"].forEach(function (code) {
      if (!DATA.locales[code]) return;
      var btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = code === "en" ? "English" : "ไทย";
      btn.setAttribute("role", "tab");
      btn.setAttribute("aria-selected", code === state.lang ? "true" : "false");
      btn.dataset.lang = code;
      btn.addEventListener("click", function () {
        if (state.lang === code) return;
        state.lang = code;
        state.searchQuery = "";
        if (els.searchInput) els.searchInput.value = "";
        render();
      });
      els.langTabs.appendChild(btn);
    });
  }

  function renderRoleTabs() {
    els.roleTabs.innerHTML = "";
    var rmap = roles();
    Object.keys(rmap).forEach(function (key) {
      var r = rmap[key];
      var btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = r.title;
      btn.setAttribute("role", "tab");
      btn.setAttribute("aria-selected", key === state.role ? "true" : "false");
      btn.dataset.role = key;
      btn.addEventListener("click", function () {
        state.role = key;
        var start = rmap[key].start_page;
        if (start && pageById(start)) state.pageId = start;
        state.stageId = null;
        state.folderId = null;
        render();
      });
      els.roleTabs.appendChild(btn);
    });
  }

  function renderNavPages() {
    els.navPages.innerHTML = "";
    var startPage = roles()[state.role].start_page;
    var u = ui();
    pages().forEach(function (p) {
      var li = document.createElement("li");
      var btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = p.title;
      if (p.id === startPage) btn.classList.add("start-here");
      if (p.id === state.pageId) btn.classList.add("active");
      if (p.generated) {
        var badge = document.createElement("span");
        badge.className = "badge badge-generated";
        badge.textContent = u.badge_generated || "generated";
        btn.appendChild(document.createTextNode(" "));
        btn.appendChild(badge);
      }
      btn.addEventListener("click", function () {
        state.pageId = p.id;
        state.stageId = null;
        state.folderId = null;
        render();
      });
      li.appendChild(btn);
      els.navPages.appendChild(li);
    });
  }

  function renderNavStages() {
    els.navStages.innerHTML = "";
    var u = ui();
    stages().forEach(function (s) {
      var li = document.createElement("li");
      var btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = s.title;
      if (s.id === state.stageId) btn.classList.add("active");
      if (s.optional) {
        var badge = document.createElement("span");
        badge.className = "badge badge-optional";
        badge.textContent = u.badge_optional || "opt";
        btn.appendChild(document.createTextNode(" "));
        btn.appendChild(badge);
      }
      btn.addEventListener("click", function () {
        state.stageId = s.id;
        state.folderId = null;
        if (s.page && pageById(s.page)) state.pageId = s.page;
        render();
      });
      li.appendChild(btn);
      els.navStages.appendChild(li);
    });
  }

  function renderNavFolders() {
    els.navFoldersList.innerHTML = "";
    var u = ui();
    folders().forEach(function (f) {
      var li = document.createElement("li");
      var btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = f.path;
      if (f.id === state.folderId) btn.classList.add("active");
      if (!f.editable) {
        var badge = document.createElement("span");
        badge.className = "badge badge-no-edit";
        badge.textContent = u.badge_pipeline || "pipeline";
        btn.appendChild(document.createTextNode(" "));
        btn.appendChild(badge);
      }
      btn.addEventListener("click", function () {
        state.folderId = f.id;
        state.stageId = null;
        render();
      });
      li.appendChild(btn);
      els.navFoldersList.appendChild(li);
    });
  }

  function renderRoleBrief() {
    var r = roles()[state.role];
    els.roleBrief.innerHTML =
      "<strong>" + escapeHtml(r.title) + "</strong>" + escapeHtml(r.brief);
  }

  function escapeHtml(s) {
    var d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
  }

  var linkMap = {
    "national_picture.md": "national",
    "pipeline_operators.md": "operator",
    "stakeholder_overview.md": "stakeholder",
    "repository_map.md": "repository",
    "data_dictionary.md": "data_dictionary",
    "handoff_ds_ml_qa.md": "handoff_ds_ml_qa",
    "coverage_report.md": "coverage_report",
    "file_type_catalog.md": "file_type_catalog",
    "index.md": "index",
  };

  function formatChartNum(v) {
    var u = ui();
    if (v === null || v === undefined || Number.isNaN(v)) return u.chart_null || "—";
    return Math.round(v).toLocaleString();
  }

  function lineSegments(months, key, xAt, yAt) {
    var segments = [];
    var current = [];
    months.forEach(function (m, i) {
      var v = m[key];
      if (v === null || v === undefined) {
        if (current.length) {
          segments.push(current);
          current = [];
        }
        return;
      }
      current.push({ x: xAt(i), y: yAt(v) });
    });
    if (current.length) segments.push(current);
    return segments;
  }

  function segmentsToPath(segments) {
    return segments
      .map(function (pts) {
        if (!pts.length) return "";
        var d = "M" + pts[0].x.toFixed(1) + "," + pts[0].y.toFixed(1);
        for (var j = 1; j < pts.length; j++) {
          d += "L" + pts[j].x.toFixed(1) + "," + pts[j].y.toFixed(1);
        }
        return d;
      })
      .filter(Boolean)
      .join(" ");
  }

  function renderNationalChart() {
    var u = ui();
    var series = DATA.nationalSeries || { available: false, months: [] };
    if (!series.available || !series.months.length) {
      return (
        '<div class="national-chart-wrap"><p class="chart-empty">' +
        escapeHtml(u.chart_no_data || "") +
        "</p></div>"
      );
    }
    var months = series.months;
    var n = months.length;
    if (n < 2) {
      return (
        '<div class="national-chart-wrap"><p class="chart-empty">' +
        escapeHtml(u.chart_no_data || "") +
        "</p></div>"
      );
    }

    var tourismMax = 0;
    var rainMax = 0;
    months.forEach(function (m) {
      if (m.guest_arrivals != null) tourismMax = Math.max(tourismMax, m.guest_arrivals);
      if (m.visitors_total != null) tourismMax = Math.max(tourismMax, m.visitors_total);
      if (m.avg_rain_mm != null) rainMax = Math.max(rainMax, m.avg_rain_mm);
    });
    if (tourismMax <= 0) tourismMax = 1;
    if (rainMax <= 0) rainMax = 1;

    var W = 860;
    var H = 400;
    var ml = 56;
    var mr = 56;
    var mt = 40;
    var mb = 48;
    var iw = W - ml - mr;
    var ih = H - mt - mb;

    function xAt(i) {
      return ml + (i / (n - 1)) * iw;
    }
    function yTour(v) {
      return mt + ih - (v / tourismMax) * ih;
    }
    function yRain(v) {
      return mt + ih - (v / rainMax) * ih;
    }

    var covidStart = -1;
    var covidEnd = -1;
    months.forEach(function (m, i) {
      if (m.is_covid) {
        if (covidStart < 0) covidStart = i;
        covidEnd = i;
      }
    });

    var svg = "";
    svg += '<svg class="national-chart" viewBox="0 0 ' + W + " " + H + '" role="img" aria-label="National monthly chart">';
    svg += '<rect x="' + ml + '" y="' + mt + '" width="' + iw + '" height="' + ih + '" fill="#faf9f7" stroke="#d8d4cb"/>';

    if (covidStart >= 0 && covidEnd >= covidStart) {
      var rx = xAt(covidStart);
      var rw = xAt(covidEnd) - rx;
      svg +=
        '<rect x="' +
        rx.toFixed(1) +
        '" y="' +
        mt +
        '" width="' +
        rw.toFixed(1) +
        '" height="' +
        ih +
        '" class="chart-covid-band" opacity="0.35"/>';
    }

    var guestSegs = lineSegments(months, "guest_arrivals", xAt, yTour);
    var visitorSegs = lineSegments(months, "visitors_total", xAt, yTour);
    var rainSegs = lineSegments(months, "avg_rain_mm", xAt, yRain);

    svg +=
      '<path d="' +
      segmentsToPath(guestSegs) +
      '" fill="none" stroke="#1e5a8a" stroke-width="2" class="chart-line-guests"/>';
    svg +=
      '<path d="' +
      segmentsToPath(visitorSegs) +
      '" fill="none" stroke="#2d6a4f" stroke-width="2" class="chart-line-visitors"/>';
    svg +=
      '<path d="' +
      segmentsToPath(rainSegs) +
      '" fill="none" stroke="#9a6700" stroke-width="1.75" stroke-dasharray="4 3" class="chart-line-rain"/>';

    for (var li = 0; li <= 4; li++) {
      var ty = mt + ih - (li / 4) * ih;
      var tval = (tourismMax * li) / 4;
      svg += '<line x1="' + ml + '" y1="' + ty + '" x2="' + (ml + iw) + '" y2="' + ty + '" stroke="#e8e4dc"/>';
      svg +=
        '<text x="' +
        (ml - 6) +
        '" y="' +
        (ty + 4) +
        '" text-anchor="end" class="chart-axis-label">' +
        escapeHtml(formatChartNum(tval)) +
        "</text>";
      var rval = (rainMax * li) / 4;
      svg +=
        '<text x="' +
        (ml + iw + 6) +
        '" y="' +
        (ty + 4) +
        '" text-anchor="start" class="chart-axis-label chart-axis-rain">' +
        escapeHtml(formatChartNum(rval)) +
        "</text>";
    }

    for (var xi = 0; xi < n; xi++) {
      if (xi % 12 !== 0 && xi !== n - 1) continue;
      var xm = xAt(xi);
      var label = months[xi].year_month.slice(0, 4);
      svg +=
        '<text x="' +
        xm +
        '" y="' +
        (H - 12) +
        '" text-anchor="middle" class="chart-axis-label">' +
        escapeHtml(label) +
        "</text>";
    }

    var colW = iw / n;
    for (var hi = 0; hi < n; hi++) {
      svg +=
        '<rect class="chart-hit-rect" data-index="' +
        hi +
        '" x="' +
        (ml + hi * colW).toFixed(1) +
        '" y="' +
        mt +
        '" width="' +
        colW.toFixed(1) +
        '" height="' +
        ih +
        '" fill="transparent"/>';
    }

    svg += '<text x="' + ml + '" y="' + (mt - 14) + '" class="chart-legend chart-legend-guests">● ' + escapeHtml(u.chart_legend_guests || "Guests") + "</text>";
    svg +=
      '<text x="' +
      (ml + 160) +
      '" y="' +
      (mt - 14) +
      '" class="chart-legend chart-legend-visitors">● ' +
      escapeHtml(u.chart_legend_visitors || "Visitors") +
      "</text>";
    svg +=
      '<text x="' +
      (ml + 300) +
      '" y="' +
      (mt - 14) +
      '" class="chart-legend chart-legend-rain">— ' +
      escapeHtml(u.chart_legend_rain || "Rain") +
      "</text>";
    if (covidStart >= 0) {
      svg +=
        '<text x="' +
        (ml + 440) +
        '" y="' +
        (mt - 14) +
        '" class="chart-legend chart-legend-covid">▭ ' +
        escapeHtml(u.chart_covid_band || "COVID") +
        "</text>";
    }
    svg += "</svg>";

    return (
      '<div class="national-chart-wrap">' +
      svg +
      '<div class="chart-tooltip" id="national-chart-tooltip" hidden></div>' +
      '<p class="chart-axis-caption">' +
      escapeHtml(u.chart_axis_tourism || "") +
      " · " +
      escapeHtml(u.chart_axis_rain || "") +
      "</p></div>"
    );
  }

  function bindNationalChartHover() {
    var tooltip = document.getElementById("national-chart-tooltip");
    var series = DATA.nationalSeries;
    if (!tooltip || !series || !series.months) return;
    var u = ui();
    els.docBody.querySelectorAll(".chart-hit-rect").forEach(function (rect) {
      rect.addEventListener("mouseenter", function () {
        var idx = parseInt(rect.getAttribute("data-index"), 10);
        var m = series.months[idx];
        if (!m) return;
        tooltip.hidden = false;
        tooltip.innerHTML =
          "<strong>" +
          escapeHtml(u.chart_hover_month || "Month") +
          ":</strong> " +
          escapeHtml(m.year_month) +
          "<br>" +
          escapeHtml(u.chart_legend_guests || "Guests") +
          ": " +
          escapeHtml(formatChartNum(m.guest_arrivals)) +
          "<br>" +
          escapeHtml(u.chart_legend_visitors || "Visitors") +
          ": " +
          escapeHtml(formatChartNum(m.visitors_total)) +
          "<br>" +
          escapeHtml(u.chart_legend_rain || "Rain") +
          ": " +
          escapeHtml(formatChartNum(m.avg_rain_mm));
      });
      rect.addEventListener("mousemove", function (ev) {
        var wrap = els.docBody.querySelector(".national-chart-wrap");
        if (!wrap) return;
        var box = wrap.getBoundingClientRect();
        tooltip.style.left = ev.clientX - box.left + 12 + "px";
        tooltip.style.top = ev.clientY - box.top + 12 + "px";
      });
      rect.addEventListener("mouseleave", function () {
        tooltip.hidden = true;
      });
    });
  }

  var mermaidReady = false;

  function typesetMermaid() {
    if (typeof mermaid === "undefined") return;
    var blocks = els.docBody.querySelectorAll("pre > code.language-mermaid");
    if (!blocks.length) return;
    if (!mermaidReady) {
      mermaid.initialize({ startOnLoad: false, theme: "neutral", securityLevel: "loose" });
      mermaidReady = true;
    }
    blocks.forEach(function (code) {
      var pre = code.parentElement;
      if (!pre || !pre.parentNode) return;
      var div = document.createElement("div");
      div.className = "mermaid";
      div.textContent = code.textContent;
      pre.parentNode.replaceChild(div, pre);
    });
    try {
      mermaid.run({ querySelector: ".doc-body .mermaid:not([data-processed])", suppressErrors: true });
    } catch (e) {
      /* offline or parse error: mermaid source may remain visible */
    }
  }

  function renderDoc() {
    var p = pageById(state.pageId);
    var html = p.html || "<p>Page not found.</p>";
    if (state.searchQuery.length >= 2) {
      html = highlightSearch(html, state.searchQuery);
    }
    if (state.pageId === "national") {
      html = renderNationalChart() + html;
    }
    els.docBody.innerHTML = html;
    if (state.pageId === "national") {
      bindNationalChartHover();
    }
    typesetMermaid();
    els.docBody.querySelectorAll("a[href]").forEach(function (a) {
      var href = a.getAttribute("href");
      if (!href || href.indexOf("http") === 0) return;
      var file = href.split("/").pop().split("#")[0];
      var target = linkMap[file];
      if (target) {
        a.addEventListener("click", function (e) {
          e.preventDefault();
          state.pageId = target;
          render();
        });
      }
    });
  }

  function highlightSearch(html, q) {
    var re = new RegExp("(" + q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + ")", "gi");
    return html.replace(/>([^<]+)</g, function (match, text) {
      if (!re.test(text)) {
        re.lastIndex = 0;
        return match;
      }
      re.lastIndex = 0;
      var marked = text.replace(re, "<mark class=\"search-hit\">$1</mark>");
      return ">" + marked + "<";
    });
  }

  function runSearch(query) {
    state.searchQuery = query.trim();
    els.searchResults.hidden = true;
    els.searchResults.innerHTML = "";
    if (state.searchQuery.length < 2) {
      renderDoc();
      return;
    }
    var q = state.searchQuery.toLowerCase();
    var hits = [];
    pages().forEach(function (p) {
      if ((p.plain || "").toLowerCase().indexOf(q) !== -1) hits.push(p);
    });
    if (hits.length) {
      els.searchResults.hidden = false;
      var label = (ui().search_matches || "Search matches") + " (" + hits.length + "):";
      els.searchResults.textContent = label;
      var ul = document.createElement("ul");
      hits.forEach(function (p) {
        var li = document.createElement("li");
        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "search-hit-link";
        btn.textContent = p.title;
        btn.addEventListener("click", function () {
          state.pageId = p.id;
          render();
          els.searchInput.focus();
        });
        li.appendChild(btn);
        ul.appendChild(li);
      });
      els.searchResults.appendChild(ul);
    }
    renderDoc();
  }

  function renderDetail() {
    var u = ui();
    var html = "";
    if (state.stageId) {
      var s = stageById(state.stageId);
      if (s) {
        html += "<h2>" + escapeHtml(s.title) + "</h2>";
        html += "<dl class=\"detail-meta\">";
        html += metaRow(u.when || "When", s.when);
        html += metaRow(u.inputs || "Inputs", s.inputs);
        html += metaRow(u.outputs || "Outputs", s.outputs);
        if (s.notes) html += metaRow(u.notes || "Notes", s.notes);
        html += "</dl>";
        if (s.command) {
          html +=
            "<dt>" +
            escapeHtml(u.command || "Command") +
            "</dt><dd><div class=\"cmd-block\"><code id=\"cmd-text\">" +
            escapeHtml(s.command) +
            "</code><button type=\"button\" id=\"copy-cmd\">" +
            escapeHtml(u.copy || "Copy") +
            "</button></div></dd>";
        }
        if (s.page) {
          var pg = pageById(s.page);
          html +=
            "<p><button type=\"button\" class=\"link-page\" data-page=\"" +
            s.page +
            "\">" +
            escapeHtml(u.open_handbook || "Open handbook:") +
            " " +
            escapeHtml(pg.title) +
            "</button></p>";
        }
      }
    } else if (state.folderId) {
      var f = folderById(state.folderId);
      if (f) {
        html += "<h2>" + escapeHtml(f.path) + "</h2>";
        html += "<dl class=\"detail-meta\">";
        html += metaRow(u.purpose || "Purpose", f.purpose);
        html += metaRow(
          u.edit_in_repo || "Edit in repo",
          f.editable ? u.edit_yes || "Yes" : u.edit_no || "No"
        );
        html += "</dl>";
      }
    } else {
      html = "<p class=\"empty\">" + escapeHtml(u.detail_empty || "") + "</p>";
    }
    els.detailPane.innerHTML = html;

    var copyBtn = document.getElementById("copy-cmd");
    if (copyBtn) {
      copyBtn.addEventListener("click", function () {
        var cmd = document.getElementById("cmd-text");
        if (!cmd) return;
        navigator.clipboard.writeText(cmd.textContent).then(function () {
          copyBtn.textContent = u.copied || "Copied";
          setTimeout(function () {
            copyBtn.textContent = u.copy || "Copy";
          }, 1500);
        });
      });
    }
    var linkPage = els.detailPane.querySelector(".link-page");
    if (linkPage) {
      linkPage.addEventListener("click", function () {
        state.pageId = linkPage.dataset.page;
        render();
      });
    }
  }

  function metaRow(label, value) {
    if (!value) return "";
    return "<dt>" + escapeHtml(label) + "</dt><dd>" + escapeHtml(String(value)) + "</dd>";
  }

  function render() {
    writeHash();
    applyChrome();
    renderLangTabs();
    renderRoleTabs();
    renderNavPages();
    renderNavStages();
    renderNavFolders();
    renderRoleBrief();
    renderDoc();
    renderDetail();
  }

  els.searchInput.addEventListener("input", function () {
    runSearch(els.searchInput.value);
  });

  window.addEventListener("hashchange", function () {
    parseHash();
    if (!pageById(state.pageId)) {
      state.pageId = roles()[state.role].start_page || "index";
    }
    render();
  });

  function init() {
    parseHash();
    if (!pageById(state.pageId)) {
      state.pageId = roles()[state.role].start_page || "index";
    }
    render();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
