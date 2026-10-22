// Browser regression probe: late (stale) Security/Sensitivity responses must never be applied.
//
// Injected after ui/index.html by tests/test_browser_stale_responses.py. Response order is
// controlled at the HTTP level: the test proxy between Chrome and the app holds the real API
// response of a registered request until the probe releases it, or then answers HTTP 500.
// The browser's own fetch and the application code are untouched.
// Synchronisation is deterministic: the probe calls the app's own async handlers and awaits
// their promises, so every check runs exactly after the stale response has been processed.
(async () => {
  const results = [];
  const check = (name, cond, detail) => results.push({ name, pass: !!cond, detail: detail ?? null });
  const stopAt = new URLSearchParams(location.search).get("stop");
  const $ = (id) => document.getElementById(id);
  const count = (selector) => document.querySelectorAll(selector).length;
  // Waiting is network-driven: Chrome's headless virtual time stops while a held request is
  // pending, so timers (setTimeout/rAF) would never fire. Each poll is a round trip to the
  // test proxy instead; there are no fixed delays.
  const nextTick = () => fetch("/__probe__/tick").then((res) => res.json());

  // ---- response gates (held by the test proxy) ------------------------------------------
  const control = (path, init) => fetch(`/__probe__/${path}`, init).then((res) => res.json());
  async function gate(match, { fail = false } = {}) {
    const { id } = await control("gates", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ match, fail }),
    });
    // Long-poll: resolves once the proxy has received the matching app request.
    const requested = control(`gates/${id}?wait=1`);
    return { id, match, requested, release: () => control(`gates/${id}/release`, { method: "POST" }) };
  }

  // ---- spies on the apply functions (global function bindings) ------------------------
  const calls = {};
  for (const name of ["applySecurityResult", "applyMongoliaSecurityResult", "applySensitivityResult", "applyMongoliaSensitivityResult"]) {
    const original = window[name];
    calls[name] = 0;
    window[name] = function (...args) { calls[name] += 1; return original.apply(this, args); };
  }
  const snapshot = () => ({ ...calls });
  const delta = (before, name) => calls[name] - before[name];

  // ---- helpers -------------------------------------------------------------------------
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
  const securityText = () => $("security-result").textContent;
  const sensitivityText = () => $("sensitivity-result").textContent;
  const outageHighlighted = (lineId) => !!document.querySelector(`.network-line.outage-line[data-line-id="${lineId}"]`);
  const monitoredHighlighted = (lineId) => !!document.querySelector(`.network-line.monitored-line[data-line-id="${lineId}"]`);
  const mnOverlayCount = () => count(".outage-line, .violation-line, .isolated-bus, .isolated-line, .monitored-line, .candidate-marker, .monitored-marker");
  const finish = () => {
    const pre = document.createElement("pre");
    pre.id = "__probe_result";
    pre.textContent = JSON.stringify({ results, stoppedAt: stopAt || null });
    document.body.appendChild(pre);
  };
  const scenario = async (name, body) => {
    try { await body(); } catch (error) { check(`${name}: exception`, false, String(error && error.stack || error)); }
    return stopAt === name;
  };

  try {
    await waitUntil(() => currentMapData && pfPreview, "initial KPG load");
    await switchGrid("mn-osm-35k", true);

    // ---- 1. Mongolia Security: A pending, input edited to B, A arrives ----------------
    if (await scenario("mn_security", async () => {
      type("outage-line", "484333456-1_0");                       // A: islanding case (rich overlays)
      const gateA = await gate("/api/grids/mn-osm-35k/security-analysis?outage_line_id=484333456-1_0");
      const before = snapshot();
      const runA = runSecurityAnalysis();
      await gateA.requested;
      type("outage-line", "751261406-1_0");                       // edit to B while A is pending
      gateA.release();
      await runA;                                                 // A fully processed by the app
      check("MN Security: stale A not applied", delta(before, "applyMongoliaSecurityResult") === 0);
      check("MN Security: no result kept", latestSecurityResult === null);
      check("MN Security: no outage/island overlays from A", mnOverlayCount() === 0, mnOverlayCount());
      check("MN Security: panel asks for a new run", securityText().includes("사고 선로가 변경"), securityText());
      const runB = runSecurityAnalysis();
      await runB;
      check("MN Security: B applied once", delta(before, "applyMongoliaSecurityResult") === 1);
      check("MN Security: B outage highlighted", outageHighlighted("751261406-1_0") && !outageHighlighted("484333456-1_0"));
      check("MN Security: B has no island overlay", count(".isolated-bus, .isolated-line") === 0);
      check("MN Security: B panel", securityText().includes("751261406-1_0") && securityText().includes("CONVERGED"), securityText().slice(0, 120));
      check("MN Security: Sensitivity enabled for B", !$("sensitivity-button").disabled);
    })) return finish();

    // ---- 2a. Mongolia Sensitivity: pending, monitored line edited ----------------------
    if (await scenario("mn_sensitivity_monitored", async () => {
      type("monitored-line", "303374638-1_0");
      const gateS = await gate("/api/grids/mn-osm-35k/sensitivity-analysis?");
      const before = snapshot();
      const runS = runSensitivityAnalysis();
      await gateS.requested;
      type("monitored-line", "484276912-1_0");
      gateS.release();
      await runS;
      check("MN Sensitivity(monitored edit): stale ranking not applied", delta(before, "applyMongoliaSensitivityResult") === 0);
      check("MN Sensitivity(monitored edit): no monitored/generator markers", count(".monitored-line, .candidate-marker, .monitored-marker") === 0);
      check("MN Sensitivity(monitored edit): no ranking text", !sensitivityText().includes("Top Candidates"), sensitivityText().slice(0, 80));
      check("MN Sensitivity(monitored edit): Security overlay kept", outageHighlighted("751261406-1_0"));
      const runNew = runSensitivityAnalysis();
      await runNew;
      check("MN Sensitivity: new request applied once", delta(before, "applyMongoliaSensitivityResult") === 1);
      check("MN Sensitivity: new monitored line highlighted", monitoredHighlighted("484276912-1_0") && !monitoredHighlighted("303374638-1_0"));
      check("MN Sensitivity: generator rank markers drawn", count(".candidate-marker") > 0 && count(".monitored-marker") === 1);
      check("MN Sensitivity: ranking + FD in details", sensitivityText().includes("Top Candidates: 5") &&
        !!document.querySelector("#sensitivity-result details.result-details") && sensitivityText().includes("유한차분 검증") && sensitivityText().includes("결과: 통과"));
    })) return finish();

    // ---- 2b. Mongolia Sensitivity: pending, outage line edited --------------------------
    if (await scenario("mn_sensitivity_outage", async () => {
      const gateS = await gate("/api/grids/mn-osm-35k/sensitivity-analysis?");
      const before = snapshot();
      const runS = runSensitivityAnalysis();
      await gateS.requested;
      type("outage-line", "222087807-1_0");
      gateS.release();
      await runS;
      check("MN Sensitivity(outage edit): stale ranking not applied", delta(before, "applyMongoliaSensitivityResult") === 0);
      check("MN Sensitivity(outage edit): all overlays cleared", mnOverlayCount() === 0, mnOverlayCount());
      check("MN Sensitivity(outage edit): Sensitivity disabled until new Security", $("sensitivity-button").disabled);
    })) return finish();

    // ---- 4a. Grid switch while Mongolia Security and Sensitivity are pending -----------
    if (await scenario("switch_mn_to_kpg", async () => {
      type("outage-line", "751261406-1_0");
      await runSecurityAnalysis();
      type("monitored-line", "");
      const gateSec = await gate("/api/grids/mn-osm-35k/security-analysis?outage_line_id=484333456-1_0");
      const gateSens = await gate("/api/grids/mn-osm-35k/sensitivity-analysis?");
      const before = snapshot();
      const runSens = runSensitivityAnalysis();
      await gateSens.requested;
      type("outage-line", "484333456-1_0");
      // Re-run Security for the island case and keep it pending, then switch grids.
      const runSec = runSecurityAnalysis();
      await gateSec.requested;
      await switchGrid("kr-kpg193", false);
      gateSec.release(); gateSens.release();
      await Promise.all([runSec, runSens]);
      check("Switch MN→KPG: no Mongolia apply after switch",
        delta(before, "applyMongoliaSecurityResult") === 0 && delta(before, "applyMongoliaSensitivityResult") === 0);
      check("Switch MN→KPG: no Mongolia overlays on KPG map", mnOverlayCount() === 0 && count(".sensitivity-marker") === 0);
      check("Switch MN→KPG: KPG panels reset (no stale text)", securityText() === "" && sensitivityText() === "", [securityText(), sensitivityText()]);
      check("Switch MN→KPG: KPG default outage restored", $("outage-line").value === "LINE-16-28");
    })) return finish();

    // ---- 3a. KPG Security: A pending, input edited, A arrives -----------------------------
    if (await scenario("kpg_security", async () => {
      const gateA = await gate("/api/security-analysis?outage_line_id=LINE-16-28");
      const before = snapshot();
      const runA = runSecurityAnalysis();
      await gateA.requested;
      type("outage-line", "LINE-16-22");
      gateA.release();
      await runA;
      check("KPG Security: stale A not applied", delta(before, "applySecurityResult") === 0);
      check("KPG Security: no outage/violation highlight", count(".outage-line, .violation-line") === 0);
      type("outage-line", "LINE-16-28");
      await runSecurityAnalysis();
      check("KPG Security: new run applied once", delta(before, "applySecurityResult") === 1);
      check("KPG Security: LINE-16-28 outage + LINE-16-22 violation", outageHighlighted("LINE-16-28") &&
        !!document.querySelector('.network-line.violation-line[data-line-id="LINE-16-22"]'));
      check("KPG Security: Sensitivity enabled", !$("sensitivity-button").disabled);
    })) return finish();

    // ---- 3b. KPG Sensitivity: newer request succeeds, older one fails late -------------
    if (await scenario("kpg_sensitivity", async () => {
      const gateOld = await gate("/api/sensitivity-analysis?outage_line_id=LINE-16-28", { fail: true });
      const before = snapshot();
      const runOld = runSensitivityAnalysis();
      await gateOld.requested;
      const runNew = runSensitivityAnalysis();
      await runNew;
      const markersAfterNew = count(".sensitivity-marker");
      check("KPG Sensitivity: newer result applied", delta(before, "applySensitivityResult") === 1 && sensitivityText().includes("LINE-16-22"));
      check("KPG Sensitivity: shared candidate markers + monitored ring drawn", count(".candidate-marker") > 0 && count(".monitored-marker") === 1);
      gateOld.release();
      await runOld;                                               // HTTP 500 for the older request
      check("KPG Sensitivity: late failure ignored", !sensitivityText().includes("실패") && sensitivityText().includes("LINE-16-22"), sensitivityText().slice(0, 80));
      check("KPG Sensitivity: markers unchanged by late failure", count(".sensitivity-marker") === markersAfterNew);
      check("KPG Security overlay unchanged", outageHighlighted("LINE-16-28"));
    })) return finish();

    // ---- 3c. KPG generator contingency still works (functional check only) ------------
    if (await scenario("kpg_generator", async () => {
      type("outage-generator", "GEN-124#1");
      $("outage-generator").dispatchEvent(new Event("change"));
      await runGeneratorContingencyAnalysis();
      const text = $("generator-contingency-result").textContent;
      check("KPG Generator contingency: result rendered", !/실패|분석 중/.test(text) && text.length > 20, text.slice(0, 120));
    })) return finish();

    // ---- 4b. Grid switch while KPG Security and Sensitivity are pending ---------------
    if (await scenario("switch_kpg_to_mn", async () => {
      const gateSens = await gate("/api/sensitivity-analysis?outage_line_id=LINE-16-28");
      const gateSec = await gate("/api/security-analysis?outage_line_id=LINE-16-28");
      const before = snapshot();
      const runSens = runSensitivityAnalysis();
      await gateSens.requested;
      const runSec = runSecurityAnalysis();
      await gateSec.requested;
      await switchGrid("mn-osm-35k", true);
      gateSec.release(); gateSens.release();
      await Promise.all([runSec, runSens]);
      check("Switch KPG→MN: no KPG apply after switch",
        delta(before, "applySecurityResult") === 0 && delta(before, "applySensitivityResult") === 0);
      check("Switch KPG→MN: no KPG overlays/markers on Mongolia map", count(".outage-line, .violation-line, .sensitivity-marker") === 0);
      check("Switch KPG→MN: Mongolia panels show defaults", !securityText().includes("LINE-16") && !sensitivityText().includes("LINE-16"), securityText());
      type("outage-line", "751261406-1_0");
      await runSecurityAnalysis();
      check("Switch KPG→MN: Mongolia Security works afterwards", outageHighlighted("751261406-1_0"));
    })) return finish();
  } catch (error) {
    check("probe exception", false, String(error && error.stack || error));
  }
  finish();
})();
