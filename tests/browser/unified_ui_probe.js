// Browser regression probe (Issue #5): KPG and Mongolia use the same Security/Sensitivity result
// components, the same map overlay language and the same flow; only the data differs.
// Injected by tests/test_browser_stale_responses.py (?probe=unified). Uses real API responses.
(async () => {
  const results = [];
  const check = (name, cond, detail) => results.push({ name, pass: !!cond, detail: detail ?? null });
  const stopAt = new URLSearchParams(location.search).get("stop");
  const $ = (id) => document.getElementById(id);
  const count = (selector) => document.querySelectorAll(selector).length;
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
  // Visible (non-details) text of a result card, one line per row/item line.
  const baseLines = (id) => {
    const card = $(id).querySelector(".result-card");
    if (!card) return [];
    const clone = card.cloneNode(true);
    clone.querySelectorAll("details").forEach((node) => node.remove());
    return [...clone.querySelectorAll(".result-row, .result-item-title, .result-item-sub, .result-item-value, .result-alert")]
      .map((node) => node.textContent.replace(/\s+/g, " ").trim());
  };
  // Component skeleton: element classes and row fields, independent of values.
  const skeleton = (id) => {
    const card = $(id).querySelector(".result-card");
    if (!card) return null;
    return {
      kind: card.dataset.resultKind,
      rows: [...card.querySelectorAll(":scope > .result-rows > .result-row")].map((row) => row.dataset.field),
      rowParts: [...card.querySelectorAll(":scope > .result-rows > .result-row")].map((row) =>
        [...row.children].map((child) => child.className).join("+")),
      item: (() => {
        const item = card.querySelector(".result-items > .result-item");
        return item ? [...item.children].map((child) => `${child.className}${child.dataset.field ? ":" + child.dataset.field : ""}`) : [];
      })(),
      blocks: [...card.children].map((child) => child.className),
    };
  };
  const legendKeys = () => [...document.querySelectorAll("#analysis-legend .analysis-legend-item")].map((n) => n.dataset.legend);
  const visible = (el) => !!el && getComputedStyle(el).display !== "none";
  const norm = (c) => { const s = document.createElementNS("http://www.w3.org/2000/svg", "line"); s.style.stroke = c; $("network-map").appendChild(s); const v = getComputedStyle(s).stroke; s.remove(); return v; };
  const lineStroke = (id) => getComputedStyle(document.querySelector(`.network-line[data-line-id="${id}"]`)).stroke;
  const shots = {};
  const finish = () => {
    if (stopAt && stopAt.endsWith("_details")) document.querySelectorAll(".result-details").forEach((d) => { d.open = true; });
    const pre = document.createElement("pre");
    pre.id = "__probe_result";
    pre.textContent = JSON.stringify({ results, shots, stoppedAt: stopAt || null });
    document.body.appendChild(pre);
  };
  const stop = (name) => stopAt === name || stopAt === `${name}_details`;

  try {
    await waitUntil(() => currentMapData && pfPreview, "initial KPG load");
    check("KPG equipment legend from model generators", $("plant-legend").textContent.startsWith("발전기 · ") && visible($("plant-legend")), $("plant-legend").textContent);

    // ---- KPG Security -------------------------------------------------------------------
    type("outage-line", "LINE-16-28");
    await runSecurityAnalysis();
    const kpgSec = baseLines("security-result");
    shots.kpgSecurity = kpgSec;
    check("KPG Security: KPG bullet format", JSON.stringify(kpgSec) === JSON.stringify([
      "Outage: LINE-16-28", "Status: CONVERGED", "Violations: 1",
      "Violated Equipment: LINE-16-22", "Loading: 103.21 %", "Limit: 1906.00 MVA", "Post-contingency: 1967.28 MVA",
    ]), kpgSec);
    const kpgSecSkeleton = skeleton("security-result");
    check("KPG Security: outage red + violation amber", lineStroke("LINE-16-28") === norm("#dc2626") && lineStroke("LINE-16-22") === norm("#f59e0b"));
    check("KPG Security: analysis legend lists outage + violation", JSON.stringify(legendKeys()) === JSON.stringify(["outage", "violation"]), legendKeys());
    if (stop("kpg_security")) return finish();

    // ---- KPG Sensitivity (auto monitored line) ------------------------------------------
    type("monitored-line", "");
    await runSensitivityAnalysis();
    const kpgSens = baseLines("sensitivity-result");
    shots.kpgSensitivity = kpgSens;
    check("KPG Sensitivity: header rows", kpgSens[0] === "Target Line: LINE-16-22" && kpgSens[1] === "Top Candidates: 5", kpgSens.slice(0, 2));
    check("KPG Sensitivity: first candidate", kpgSens[2] === "1. GEN-19#0" && kpgSens[3] === "Bus 19 · 마포용산" && kpgSens[4] === "민감도: 0.4211", kpgSens.slice(2, 5));
    check("KPG Sensitivity: 5 candidate items", count("#sensitivity-result .result-item") === 5);
    const kpgSensSkeleton = skeleton("sensitivity-result");
    check("KPG Sensitivity: monitored line magenta + ring", lineStroke("LINE-16-22") === norm("#db2777") && count(".monitored-marker") === 1);
    check("KPG Sensitivity: rank markers (shared style)", count(".candidate-marker") > 0 &&
      document.querySelector(".candidate-marker text").textContent === "1");
    check("KPG Sensitivity: legend adds monitored + candidate", ["outage", "violation", "monitored", "candidate"].every((k) => legendKeys().includes(k)), legendKeys());
    if (stop("kpg_sensitivity")) return finish();

    // Layer toggle hides every analysis overlay and the analysis legend.
    const layer = $("layer-analysis");
    layer.checked = false; layer.dispatchEvent(new Event("change"));
    check("Layer off: analysis legend + markers hidden", !visible($("analysis-legend")) && !visible(document.querySelector(".candidate-marker")));
    check("Layer off: lines back to base colours", lineStroke("LINE-16-28") !== norm("#dc2626") && lineStroke("LINE-16-22") !== norm("#db2777"));
    layer.checked = true; layer.dispatchEvent(new Event("change"));

    // ---- Mongolia Security ------------------------------------------------------------------
    await switchGrid("mn-osm-35k", true);
    check("Switch: results and overlays reset", count(".result-card, .outage-line, .candidate-marker, .monitored-marker") === 0 && $("analysis-legend").hidden);
    check("MN equipment legend from power-plant records", $("plant-legend").textContent.startsWith("발전소 데이터 · "), $("plant-legend").textContent);
    type("outage-line", "751261406-1_0");
    await runSecurityAnalysis();
    const mnSec = baseLines("security-result");
    shots.mnSecurity = mnSec;
    check("MN Security: KPG bullet format", JSON.stringify(mnSec) === JSON.stringify([
      "Outage: 751261406-1_0 (10–25, 220 kV)", "Status: CONVERGED", "Violations: 0",
    ]), mnSec);
    const mnSecSkeleton = skeleton("security-result");
    check("Same Security components (row fields + row parts)",
      JSON.stringify([mnSecSkeleton.kind, mnSecSkeleton.rows, mnSecSkeleton.rowParts]) === JSON.stringify([kpgSecSkeleton.kind, kpgSecSkeleton.rows, kpgSecSkeleton.rowParts]),
      { kpg: kpgSecSkeleton, mn: mnSecSkeleton });
    const mnSecCard = $("security-result").querySelector(".result-card");
    check("MN Security: diagnostics only in collapsed details", !!mnSecCard.querySelector("details.result-details:not([open])") &&
      !baseLines("security-result").join(" ").includes("최대 전압 변화") && mnSecCard.querySelector("details").textContent.includes("최대 전압 변화"));
    check("MN Security: no repeated notice in the card; disclaimer kept in model info", !mnSecCard.querySelector(".result-notice")
      && $("analysis-interpretation").textContent.includes("실제 몽골 운영 데이터가 아닙니다") && $("pf-preview-disclaimer").textContent.includes("실제 몽골 운영 데이터가 아닙니다"));
    check("MN Security: outage red, no violation", lineStroke("751261406-1_0") === norm("#dc2626") && count(".violation-line") === 0);
    check("MN Security: analysis legend lists outage only", JSON.stringify(legendKeys()) === JSON.stringify(["outage"]), legendKeys());
    if (stop("mn_security")) return finish();

    // ---- Mongolia Sensitivity (auto monitored line) --------------------------------------
    type("monitored-line", "");
    await runSensitivityAnalysis();
    const mnSens = baseLines("sensitivity-result");
    shots.mnSensitivity = mnSens;
    check("MN Sensitivity: header rows", mnSens[0] === "Target Line: 484276912-1_0 (13–59, 220 kV)" && mnSens[1] === "Top Candidates: 5", mnSens.slice(0, 2));
    check("MN Sensitivity: first candidate", mnSens[2] === "1. SYNTHETIC_PV_7" && mnSens[3] === "Bus 7 · 110 kV" && mnSens[4] === "민감도: -1.0074", mnSens.slice(2, 5));
    check("MN Sensitivity: 5 candidate items", count("#sensitivity-result .result-item") === 5);
    const mnSensSkeleton = skeleton("sensitivity-result");
    check("Same Sensitivity components (rows + candidate item)",
      JSON.stringify([mnSensSkeleton.rows, mnSensSkeleton.rowParts, mnSensSkeleton.item]) === JSON.stringify([kpgSensSkeleton.rows, kpgSensSkeleton.rowParts, kpgSensSkeleton.item]),
      { kpg: kpgSensSkeleton, mn: mnSensSkeleton });
    const mnSensDetails = $("sensitivity-result").querySelector("details.result-details");
    check("MN Sensitivity: FD, flows, definition, caveats in details", !!mnSensDetails && ["유한차분 검증", "사고 전후 조류", "정의", "모델 주의사항", "평가 범위"]
      .every((title) => mnSensDetails.textContent.includes(title)));
    check("MN Sensitivity: base text has no long diagnostics", !mnSens.join(" ").match(/유한차분|Slack|양단|caveat/));
    check("MN Sensitivity: monitored line magenta + ring + rank markers", lineStroke("484276912-1_0") === norm("#db2777") &&
      count(".monitored-marker") === 1 && count(".candidate-marker") > 0);
    check("MN Sensitivity: legend outage + monitored + candidate", JSON.stringify(legendKeys()) === JSON.stringify(["outage", "monitored", "candidate"]), legendKeys());
    if (stop("mn_sensitivity")) return finish();

    // ---- Mongolia islanding: concise status, Sensitivity explicitly not evaluated ----------
    type("outage-line", "484333456-1_0");
    await runSecurityAnalysis();
    const island = baseLines("security-result");
    shots.mnIsland = island;
    check("MN island: concise status rows + alert", island[0] === "Outage: 484333456-1_0 (10–19, 220 kV)" && island[1] === "Status: CONVERGED (주 계통만 수렴)" &&
      island[2] === "Violations: 0" && /^계통 분리: 모선 11개 · 합성 부하 1.80 MW · 합성 발전기 4대 · 분리 섬 PF 미실행$/.test(island[3] || ""), island);
    check("MN island: Sensitivity disabled with reason", $("sensitivity-button").disabled && $("sensitivity-result").textContent.includes("미평가"));
    check("MN island: isolated overlay + legend", count(".isolated-bus") === 11 && legendKeys().includes("isolated"), legendKeys());
    if (stop("mn_island")) return finish();

    // ---- Back to KPG: same flow still works, no Mongolia leftovers ---------------------
    await switchGrid("kr-kpg193", false);
    check("Back to KPG: clean state", count(".result-card, .isolated-bus, .candidate-marker") === 0 && $("outage-line").value === "LINE-16-28");
    await runSecurityAnalysis();
    check("Back to KPG: Security works", baseLines("security-result")[2] === "Violations: 1");

    // ---- KPG chat -> UI (ui_updates from real tool outputs; LLM tool choice scripted) -------
    const sendChat = async (text) => {
      const before = count("#chat-log .chat-message.agent");
      $("chat-message").value = text;
      await sendChatMessage();
      return count("#chat-log .chat-message.agent") === before + 1;
    };
    const restSecurityLines = JSON.stringify(baseLines("security-result"));
    type("outage-line", "LINE-16-22");                       // chat must set the outage itself
    check("Chat security: answer appended", await sendChat("[fixture:security] LINE-16-28 사고 분석"));
    check("Chat security: same card as REST result", JSON.stringify(baseLines("security-result")) === restSecurityLines, baseLines("security-result"));
    check("Chat security: outage input + overlays", $("outage-line").value === "LINE-16-28" &&
      lineStroke("LINE-16-28") === norm("#dc2626") && lineStroke("LINE-16-22") === norm("#f59e0b"));
    check("Chat sensitivity: answer appended", await sendChat("[fixture:sensitivity] 민감도 분석"));
    const chatSens = baseLines("sensitivity-result");
    check("Chat sensitivity: shared card", chatSens[0] === "Target Line: LINE-16-22" && chatSens[1] === "Top Candidates: 5" && chatSens[2] === "1. GEN-19#0", chatSens.slice(0, 3));
    check("Chat sensitivity: same component skeleton as panel run",
      JSON.stringify([skeleton("sensitivity-result").rows, skeleton("sensitivity-result").item]) === JSON.stringify([kpgSensSkeleton.rows, kpgSensSkeleton.item]));
    check("Chat sensitivity: monitored ring + rank markers", count(".monitored-marker") === 1 && count(".candidate-marker") > 0);
    check("Chat generator: answer appended", await sendChat("[fixture:generator] GEN-124#1 탈락 분석"));
    const generatorText = $("generator-contingency-result").textContent;
    check("Chat generator: result rendered", generatorText.includes("GEN-124#1") && !/실패/.test(generatorText), generatorText.slice(0, 120));
    check("Chat generator: outage marker + legend", count(".generator-outage-marker") > 0 && legendKeys().includes("generator-outage"), legendKeys());
    check("Chat redispatch: workflow answer rendered", await sendChat("[fixture:redispatch] LINE-16-28 대응방안") &&
      [...document.querySelectorAll("#chat-log .chat-message.agent")].at(-1).textContent.includes("LINE-16-28"));
    if (stop("kpg_chat")) return finish();
  } catch (error) {
    check("probe exception", false, String(error && error.stack || error));
  }
  finish();
})();
