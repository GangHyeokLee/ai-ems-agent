// Browser regression probe (Issue #6): Light/Dark/System theme and the shared help popovers on
// both grids. Injected by tests/test_browser_stale_responses.py (?probe=theme). Real API data.
// Phases (state kept in sessionStorage across reloads): 0 clears the stored theme, 1 runs the
// main checks, 2 verifies persistence after a reload.
(async () => {
  const KEY = "ai-ems-theme";
  const stopAt = new URLSearchParams(location.search).get("stop");
  const store = (name, value) => sessionStorage.setItem(`themeProbe.${name}`, JSON.stringify(value));
  const load = (name, fallback) => JSON.parse(sessionStorage.getItem(`themeProbe.${name}`) || JSON.stringify(fallback));
  const phase = sessionStorage.getItem("themeProbe.phase") || "0";
  if (phase === "0") {
    localStorage.removeItem(KEY);
    sessionStorage.setItem("themeProbe.phase", "1");
    store("results", []);
    location.reload();
    return;
  }
  const results = load("results", []);
  const check = (name, cond, detail) => results.push({ name, pass: !!cond, detail: detail ?? null });
  const $ = (id) => document.getElementById(id);
  const root = document.documentElement;
  const nextTick = () => fetch("/__probe__/tick").then((res) => res.json());
  const waitUntil = async (predicate, label, timeoutMs = 60000) => {
    const start = performance.now();
    while (!predicate()) {
      if (performance.now() - start > timeoutMs) throw new Error(`timeout: ${label}`);
      await nextTick();
    }
  };
  const type = (id, value) => { $(id).value = value; $(id).dispatchEvent(new Event("input")); };
  const switchGrid = async (id, mongolia) => {
    const select = $("grid-select");
    select.value = id;
    select.dispatchEvent(new Event("change"));
    await waitUntil(() => isMongoliaGrid() === mongolia && currentMapData && pfPreview, `grid ${id} loaded`);
  };
  const pickTheme = (value) => { const input = document.querySelector(`#theme-switch input[value="${value}"]`); input.click(); };
  // ---- colour helpers -------------------------------------------------------------------
  const rgba = (css) => { const m = css.match(/rgba?\(([^)]+)\)/); if (!m) return null; const p = m[1].split(",").map((v) => parseFloat(v)); return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 }; };
  const blend = (top, under) => ({ r: top.r * top.a + under.r * (1 - top.a), g: top.g * top.a + under.g * (1 - top.a), b: top.b * top.a + under.b * (1 - top.a), a: 1 });
  const effectiveBg = (el) => {
    const layers = [];
    for (let node = el; node && node.nodeType === 1; node = node.parentElement) {
      const c = rgba(getComputedStyle(node).backgroundColor);
      if (c && c.a > 0) { layers.push(c); if (c.a >= 1) break; }
    }
    let color = { r: 255, g: 255, b: 255, a: 1 };
    for (const layer of layers.reverse()) color = blend(layer, color);
    return color;
  };
  const lum = (c) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }; return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b); };
  const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };
  const textContrast = (el) => ratio(rgba(getComputedStyle(el).color), effectiveBg(el));
  const contrastReport = {};
  const checkText = (label, el, min = 4.5) => {
    if (!el) { check(`contrast ${label}: element present`, false); return; }
    const value = textContrast(el);
    contrastReport[label] = Number(value.toFixed(2));
    check(`contrast ${label} >= ${min}`, value >= min, value.toFixed(2));
  };
  // Line breaks between two Hangul syllables inside help text (first sentence excluded).
  const hangulBreaks = (container) => {
    const bad = [];
    const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      if (node.parentElement.closest(".help-strong")) continue;
      const text = node.textContent;
      let previousTop = null;
      for (let i = 0; i < text.length; i += 1) {
        const range = document.createRange();
        range.setStart(node, i); range.setEnd(node, i + 1);
        const rect = range.getClientRects()[0];
        if (!rect) continue;
        if (previousTop !== null && rect.top > previousTop + 2 && /[\uac00-\ud7a3]/.test(text[i - 1]) && /[\uac00-\ud7a3]/.test(text[i])) {
          bad.push(text.slice(Math.max(0, i - 6), i + 6));
        }
        previousTop = rect.top;
      }
    }
    return bad;
  };
  const lineCount = (el) => Math.round(el.getBoundingClientRect().height / parseFloat(getComputedStyle(el).lineHeight));
  const wide = innerWidth >= 900;
  const checkHelpLayout = (label, popover) => {
    const first = popover.querySelector(".help-strong");
    if (first) {
      check(`${label}: first sentence ${wide ? "on one line" : "wraps inside the viewport"}`, wide ? lineCount(first) === 1 : first.getBoundingClientRect().right <= innerWidth, [lineCount(first), first.textContent]);
    }
    const breaks = hangulBreaks(popover);
    check(`${label}: no line break inside a Korean word`, breaks.length === 0, breaks.slice(0, 5));
    const body = popover.querySelector("p:not(.help-strong), li");
    check(`${label}: keep-all + break-word`, getComputedStyle(body).wordBreak === "keep-all" && getComputedStyle(body).overflowWrap === "break-word");
    check(`${label}: inside viewport`, inViewport(popover));
  };
  const inViewport = (el) => { const r = el.getBoundingClientRect(); return r.left >= 0 && r.top >= 0 && r.right <= innerWidth + 0.5 && r.bottom <= innerHeight + 0.5 && r.width > 0; };
  const finish = () => {
    store("results", results);
    if (stopAt && stopAt.endsWith("_details")) document.querySelectorAll(".result-details").forEach((d) => { d.open = true; });
    const pre = document.createElement("pre");
    pre.id = "__probe_result";
    pre.textContent = JSON.stringify({ results, contrast: { ...load("contrast", {}), ...contrastReport }, phase, stoppedAt: stopAt || null });
    document.body.appendChild(pre);
  };

  try {
    await waitUntil(() => currentMapData && pfPreview, "initial KPG load");
    if (phase === "1") {
      // ---- System default + OS change ---------------------------------------------------
      check("Default preference is System", root.dataset.themePreference === "system" && document.querySelector('#theme-switch input[value="system"]').checked);
      check("System follows the OS (light in this run)", root.dataset.theme === (themeMedia.matches ? "dark" : "light"));
      let osDark = true;
      Object.defineProperty(themeMedia, "matches", { configurable: true, get: () => osDark });
      themeMedia.dispatchEvent(new Event("change"));
      check("OS switch to dark is applied while System", root.dataset.theme === "dark" && $("theme-current").textContent.includes("Dark"));
      osDark = false;
      themeMedia.dispatchEvent(new Event("change"));
      check("OS switch back to light is applied", root.dataset.theme === "light");
      delete themeMedia.matches;
      check("No explicit choice stored yet", localStorage.getItem(KEY) === null);
      // Explicit Light (the OS of the test machine may itself be dark).
      pickTheme("light");
      check("Light: explicit choice applied + stored", root.dataset.theme === "light" && localStorage.getItem(KEY) === "light" && getComputedStyle(root).colorScheme === "light");
      const lightBody = getComputedStyle(document.body).backgroundColor;

      // ---- Light theme help popover ----------------------------------------------------------
      const modelTrigger = $("model-help-trigger"), modelHelp = $("model-help");
      check("No always-on banner row", !$("pf-preview-notice") && !$("pf-preview-panel") && modelHelp.hidden);
      const toolbarNext = [...document.querySelectorAll("#map-panel > *")].filter((el) => getComputedStyle(el).display !== "none");
      check("No empty row: grid toolbar is followed by the PF toolbar", toolbarNext[toolbarNext.indexOf(document.querySelector(".grid-toolbar")) + 1] === $("pf-preview-toolbar"),
        toolbarNext.map((el) => el.id || el.className));
      check("Model info button next to the grid selector", modelTrigger.parentElement.classList.contains("grid-toolbar") && !modelTrigger.hidden);
      check("Normal status not shown permanently", $("grid-status").hidden && $("grid-status").textContent === "" && $("grid-capability-note").hidden && $("pf-preview-alert").hidden);
      modelTrigger.dispatchEvent(new MouseEvent("mouseenter"));
      check("Help: hover opens", !modelHelp.hidden && modelTrigger.getAttribute("aria-expanded") === "true");
      const kpgHelp = modelHelp.textContent;
      checkHelpLayout("Help KPG (light)", modelHelp);
      check("Help KPG: normal status moved into help", $("grid-status-detail").textContent === "KPG-193 지도 · 격리된 기본상태 PF 미리보기");
      check("Help KPG: display criteria + result interpretation kept", ["선로 부하율 표시: PoC 시연용 시각화 구간이며 운영사의 공식 경보 기준이 아닙니다.", "사고 결과 강조가 PF 색상보다 우선 표시됩니다",
        "모선 전압 표시: 각 모선의 하한/상한", "KPG-193 합성 시험계통 결과 · 실제 한국 운영계통 결과가 아닙니다."].every((t) => modelHelp.textContent.includes(t)), modelHelp.textContent.slice(-400));
      check("Quiet panels: no default instructions", $("security-result").textContent === "" && $("sensitivity-result").textContent === "" && $("generator-contingency-result").textContent === "");
      check("Help KPG: original details preserved", ["실제 한국 운영 데이터가 아닙니다", "AC PF 수렴", "회 반복", "모선", "선로", "발전기", "HVDC", "운영 경보 기준이 아닙니다", "KPG-193 합성 시험계통의 정상상태 PF 미리보기"]
        .every((text) => kpgHelp.includes(text)), kpgHelp.slice(0, 300));
      check("Help: inside viewport", inViewport(modelHelp));
      modelTrigger.dispatchEvent(new MouseEvent("mouseleave"));
      await waitUntil(() => modelHelp.hidden, "hover close");
      check("Help: hover leave closes", modelHelp.hidden && modelTrigger.getAttribute("aria-expanded") === "false");
      modelTrigger.click();
      modelTrigger.dispatchEvent(new MouseEvent("mouseleave"));
      await nextTick(); await nextTick();
      check("Help: click pins open", !modelHelp.hidden);
      document.body.click();
      check("Help: outside click closes", modelHelp.hidden);
      modelTrigger.focus();
      modelTrigger.click();                                      // Enter/Space on a native button
      check("Help: keyboard activation opens", !modelHelp.hidden);
      modelHelp.focus();
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
      check("Help: Escape closes and returns focus", modelHelp.hidden && document.activeElement === modelTrigger);
      check("Help: ARIA wiring", modelTrigger.getAttribute("aria-controls") === "model-help" && modelHelp.getAttribute("role") === "dialog"
        && !!$(modelHelp.getAttribute("aria-labelledby")));
      const assistantTrigger = $("assistant-help-trigger"), assistantHelp = $("assistant-help");
      assistantTrigger.click();
      const kpgGuide = assistantHelp.textContent;
      check("Assistant help KPG: scope + examples", ["LINE-16-28 사고를 분석해 줘.", "GEN-124#1 발전기 탈락을 분석해 줘.", "최적 Redispatch가 아닙니다", "실제 한국 운영계통 결과가 아닙니다"].every((t) => kpgGuide.includes(t)), kpgGuide.slice(0, 200));
      check("Assistant help: inside viewport + only one open", inViewport(assistantHelp) && modelHelp.hidden);
      checkHelpLayout("Assistant help KPG", assistantHelp);
      check("Assistant help KPG: panel usage moved here", ["지도에서 발전기 행을 클릭하면 자동으로 입력됩니다.", "감시 선로를 비우면 사고 후 최대 위반 선로를 선택합니다.", "‘분석 결과’를 해제하면 숨겨집니다"]
        .every((t) => assistantHelp.textContent.includes(t)));
      if (stopAt === "help_model_light") { document.body.click(); modelTrigger.click(); return finish(); }
      document.body.click();
      // KPG Security + Sensitivity in Light
      type("outage-line", "LINE-16-28");
      await runSecurityAnalysis();
      type("monitored-line", "");
      await runSensitivityAnalysis();
      check("Light KPG: results rendered", $("security-result").textContent.includes("Violations: 1") && $("sensitivity-result").textContent.includes("Top Candidates: 5"));
      checkText("light security row", $("security-result").querySelector(".result-row"));
      checkText("light sensitivity candidate", $("sensitivity-result").querySelector(".result-item-sub"));
      checkText("light analysis legend", document.querySelector("#analysis-legend .analysis-legend-item"));
      checkText("light grid toolbar", document.querySelector(".grid-toolbar label"));
      checkText("light chat input", $("chat-message"));
      check("Light KPG: no repeated notices or general guidance on screen", !document.querySelector(".result-notice, .pf-legend-note, .plant-note, #pf-overlay-note, .analysis-subtitle"));
      if (stopAt === "kpg_light") return finish();
      document.body.click();

      // ---- Dark theme (explicit) ---------------------------------------------------------------
      pickTheme("dark");
      check("Dark: applied + stored", root.dataset.theme === "dark" && localStorage.getItem(KEY) === "dark" && root.dataset.themePreference === "dark");
      check("Dark: body background changed", getComputedStyle(document.body).backgroundColor !== lightBody);
      check("Dark: native controls follow (color-scheme)", getComputedStyle(root).colorScheme === "dark");
      check("Dark: map country fill is dark", lum(rgba(getComputedStyle(document.querySelector(".country-outline")).fill)) < 0.05);

      // KPG Security + Sensitivity in dark
      type("outage-line", "LINE-16-28");
      await runSecurityAnalysis();
      // Measured before Sensitivity: LINE-16-22 then also becomes the monitored line (magenta wins).
      const securityBg = rgba(getComputedStyle(document.querySelector(".country-outline")).fill);
      for (const [label, selector] of [["outage", ".outage-line"], ["violation", ".violation-line"]]) {
        const value = ratio(rgba(getComputedStyle(document.querySelector(selector)).stroke), securityBg);
        contrastReport[`dark overlay ${label} vs map`] = Number(value.toFixed(2));
        check(`Dark overlay ${label} >= 3:1 against the map`, value >= 3, value.toFixed(2));
      }
      type("monitored-line", "");
      await runSensitivityAnalysis();
      check("Dark KPG: results rendered", $("security-result").textContent.includes("Violations: 1") && $("sensitivity-result").textContent.includes("Top Candidates: 5"));
      checkText("header", document.querySelector("header h1"));
      checkText("theme label", document.querySelector("#theme-switch label"));
      checkText("model info button", modelTrigger);
      checkText("security row", $("security-result").querySelector(".result-row"));
      checkText("security result row", $("security-result").querySelector(".result-row"));
      check("Dark KPG: no repeated notices or general guidance on screen", !document.querySelector(".result-notice, .pf-legend-note, .plant-note, #pf-overlay-note, .analysis-subtitle"));
      checkText("sensitivity candidate", $("sensitivity-result").querySelector(".result-item-sub"));
      checkText("analysis box heading", document.querySelector(".analysis-box-header strong"));
      checkText("analysis legend", document.querySelector("#analysis-legend .analysis-legend-item"));
      checkText("equipment legend", document.querySelector("#plant-legend .plant-legend-item"));
      checkText("layer control", document.querySelector(".map-layer-option span"));
      checkText("PF toolbar", document.querySelector("#pf-preview-toolbar label"));
      checkText("chat input", $("chat-message"));
      checkText("grid toolbar", document.querySelector(".grid-toolbar label"));
      modelTrigger.click();
      checkText("help popover text", modelHelp.querySelector("p:not(.help-strong)"));
      checkText("help popover strong", modelHelp.querySelector(".help-strong"));
      if (stopAt === "help_model_kpg_dark") return finish();
      document.body.click();
      // Map data colours vs the dark map background (graphical objects >= 3:1).
      const mapBg = rgba(getComputedStyle(document.querySelector(".country-outline")).fill);
      const lineColors = new Set([...document.querySelectorAll(".network-line")].map((el) => getComputedStyle(el).stroke));
      const busColors = new Set([...document.querySelectorAll(".network-bus")].map((el) => getComputedStyle(el).fill));
      const minLine = Math.min(...[...lineColors].map((c) => ratio(rgba(c), mapBg)));
      const minBus = Math.min(...[...busColors].map((c) => ratio(rgba(c), mapBg)));
      contrastReport["KPG map min line/bus vs background"] = [Number(minLine.toFixed(2)), Number(minBus.toFixed(2))];
      check("Dark KPG map: line & bus colours >= 3:1", minLine >= 3 && minBus >= 3, [minLine.toFixed(2), minBus.toFixed(2)]);
      check("Dark KPG overlays: outage solid, violation dashed, monitored dashed", getComputedStyle(document.querySelector(".outage-line")).strokeDasharray === "none"
        && getComputedStyle(document.querySelector(".violation-line")).strokeDasharray !== "none" && getComputedStyle(document.querySelector(".monitored-line")).strokeDasharray !== "none");
      const darkBg = rgba(getComputedStyle(document.querySelector(".country-outline")).fill);
      const monitoredValue = ratio(rgba(getComputedStyle(document.querySelector(".monitored-line")).stroke), darkBg);
      contrastReport["dark overlay monitored vs map"] = Number(monitoredValue.toFixed(2));
      check("Dark overlay monitored >= 3:1 against the map", monitoredValue >= 3, monitoredValue.toFixed(2));
      if (stopAt === "kpg_dark") return finish();

      // Grid switch keeps the theme; Mongolia in dark
      await switchGrid("mn-osm-35k", true);
      check("Theme kept across grid switch", root.dataset.theme === "dark" && document.querySelector('#theme-switch input[value="dark"]').checked);
      check("MN: no banner, normal status hidden", !$("pf-preview-notice") && $("grid-status").hidden && $("grid-capability-note").hidden);
      check("Chat log no longer seeded with the long guide", document.querySelectorAll("#chat-log .chat-message").length === 0);
      assistantTrigger.click();
      const mnGuide = assistantHelp.textContent;
      check("Assistant help MN: limits + examples preserved", ["합성 PF 조회와 선로 N-1 Security·Sensitivity를 지원합니다", "Redispatch·OPF·발전기 사고",
        "Bus 137 전압은?", "751261406-1_0 선로 N-1 후 민감도 Top 5", "이어서 민감도 후보 5개", "Security: 검증 완료", "Sensitivity: 검증 완료"].every((t) => mnGuide.includes(t)), mnGuide.slice(0, 200));
      check("Assistant help MN: no KPG text", !mnGuide.includes("LINE-16-28"));
      checkText("assistant help (MN)", assistantHelp.querySelector("li"));
      if (stopAt === "help_assistant_mn_dark") return finish();
      document.body.click();
      modelTrigger.click();
      const mnHelp = modelHelp.textContent;
      check("Help MN: original details preserved", ["실제 몽골 운영 데이터가 아닙니다", "166모선 주 연결요소", "합성 발전기 45대", "합성 리액터 20 MVAr", "실제 측정값이 아닙니다",
        "전기정수 미검증", "모델 신뢰성 검토 필요", "선로 N-1 Security Analysis"].every((t) => mnHelp.includes(t)), mnHelp.slice(0, 300));
      check("Help MN: no KPG text", !mnHelp.includes("HVDC") && !mnHelp.includes("KPG193"));
      check("Help MN: normal status moved into help", $("grid-status-detail").textContent === "합성 PF 미리보기 · 선로 N-1 Security / Sensitivity Analysis 가능");
      check("Help MN: interpretation + equipment data limits kept", ["합성 발전기 주입 민감도 · 실제 발전소·재급전 권고가 아닙니다.", "레코드 기준 · 중복 가능 · 용량/연결 검증 전"]
        .every((t) => modelHelp.textContent.includes(t)));
      check("MN: unsupported generator analysis stays visible", $("generator-contingency-result").textContent === "현재 계통은 발전기 탈락 분석을 지원하지 않습니다.");
      check("MN: no synthetic tag in the example selector", $("mn-contingency-examples").options[0].textContent === "대표 사고 선택");
      checkHelpLayout("Help MN (dark)", modelHelp);
      if (stopAt === "help_model_mn_dark") return finish();
      document.body.click();
      type("outage-line", "484333456-1_0");
      await runSecurityAnalysis();
      checkText("island alert", $("security-result").querySelector(".result-alert"));
      check("MN island: alert + not-evaluated message kept", $("security-result").querySelector(".result-alert").textContent.includes("분리 섬 PF 미실행")
        && $("sensitivity-result").textContent.includes("미평가"));
      checkText("sensitivity disabled message", $("sensitivity-result"));
      type("outage-line", "751261406-1_0");
      await runSecurityAnalysis();
      type("monitored-line", "");
      await runSensitivityAnalysis();
      check("Dark MN: results + overlays", $("sensitivity-result").textContent.includes("Top Candidates: 5") && document.querySelectorAll(".candidate-marker").length > 0
        && document.querySelectorAll(".monitored-marker").length === 1);
      checkText("details summary", $("security-result").querySelector(".result-details summary"));
      const mnBg = rgba(getComputedStyle(document.querySelector(".country-outline")).fill);
      const mnLines = new Set([...document.querySelectorAll(".network-line")].map((el) => getComputedStyle(el).stroke));
      const mnPlants = new Set([...document.querySelectorAll(".powerplant-marker")].map((el) => getComputedStyle(el).fill));
      const minMnLine = Math.min(...[...mnLines].map((c) => ratio(rgba(c), mnBg)));
      const minPlant = Math.min(...[...mnPlants].map((c) => ratio(rgba(c), mnBg)));
      contrastReport["MN map min line/plant vs background"] = [Number(minMnLine.toFixed(2)), Number(minPlant.toFixed(2))];
      check("Dark MN map: line & power-plant colours >= 3:1", minMnLine >= 3 && minPlant >= 3, [minMnLine.toFixed(2), minPlant.toFixed(2)]);
      if (stopAt === "mn_dark" || stopAt === "mn_dark_details") return finish();

      // Abnormal status stays visible; normal status returns to the help only.
      const normalStatus = $("grid-status-detail").textContent;
      setGridStatus("지도 오류: probe-forced", true);
      check("Abnormal status visible next to the selector", !$("grid-status").hidden && $("grid-status").textContent === "지도 오류: probe-forced" && $("grid-status").classList.contains("grid-warning"));
      checkText("abnormal status", $("grid-status"));
      setGridStatus(normalStatus, false);
      check("Normal status hidden again", $("grid-status").hidden);
      // Visible failure alert stays outside the popover.
      const savedError = pfPreviewError;
      pfPreviewError = "probe-forced error";
      updatePfPreviewPanel();
      check("Failure alert visible outside help", !$("pf-preview-alert").hidden && $("pf-preview-alert").textContent.includes("probe-forced error") && $("model-help").hidden);
      checkText("failure alert", $("pf-preview-alert"));
      pfPreviewError = savedError;
      updatePfPreviewPanel();
      check("Failure alert hidden again", $("pf-preview-alert").hidden);

      // PF display modes keep working in dark (data colours re-applied).
      const mode = $("pf-display-mode");
      mode.value = "line_loading"; mode.dispatchEvent(new Event("change"));
      const line = document.querySelector('.network-line[data-line-id="222087807-1_0"]');
      const normColor = (value) => { const probe = document.createElement("i"); probe.style.color = value; document.body.appendChild(probe); const out = getComputedStyle(probe).color; probe.remove(); return out; };
      const loading = pfLineResultMap.get("222087807-1_0").loading_percent;
      const darkStroke = line.style.stroke;
      check("Dark PF mode uses the dark tint of the loading band", normColor(darkStroke) === normColor(lineLoadingColor(loading)) && normColor(darkStroke) === normColor(DARK_DATA_TINTS["#16a34a"]), darkStroke);
      pickTheme("light");
      check("Switching theme re-applies data colours", normColor(line.style.stroke) === normColor("#16a34a") && root.dataset.theme === "light", line.style.stroke);
      pickTheme("dark");
      await switchGrid("kr-kpg193", false);

      store("contrast", contrastReport);
      store("results", results);
      sessionStorage.setItem("themeProbe.phase", "2");
      location.reload();
      return;
    }
    // ---- phase 2: persistence after reload --------------------------------------------------
    check("Reload: stored Dark applied", root.dataset.theme === "dark" && root.dataset.themePreference === "dark"
      && document.querySelector('#theme-switch input[value="dark"]').checked);
    pickTheme("light");
    check("Light: applied + stored", root.dataset.theme === "light" && localStorage.getItem(KEY) === "light");
    // Mongolia warnings in Light: islanding, not-evaluated Sensitivity, unsupported generator analysis.
    await switchGrid("mn-osm-35k", true);
    type("outage-line", "484333456-1_0");
    await runSecurityAnalysis();
    check("Light MN island: warnings kept", $("security-result").querySelector(".result-alert")?.textContent.includes("분리 섬 PF 미실행")
      && $("sensitivity-result").textContent.includes("미평가") && $("generator-contingency-result").textContent.includes("지원하지 않습니다"));
    checkText("light island alert", $("security-result").querySelector(".result-alert"));
    checkText("light unsupported generator message", $("generator-contingency-result"));
    if (stopAt === "mn_light_island") return finish();
    await switchGrid("kr-kpg193", false);
    pickTheme("system");
    check("System: stored + follows OS", localStorage.getItem(KEY) === "system" && root.dataset.theme === (themeMedia.matches ? "dark" : "light"));
    sessionStorage.removeItem("themeProbe.phase");
  } catch (error) {
    check("probe exception", false, String(error && error.stack || error));
  }
  finish();
})();
