// Browser regression probe: late (stale) Security/Sensitivity responses must never be applied.
//
// Injected after ui/index.html by tests/test_browser_stale_responses.py. Response order is
// controlled by intercepting window.fetch from this probe (the application code is untouched):
// a matching request is held until the probe releases it, or is answered with an HTTP 500.
// Synchronisation is deterministic: the probe calls the app's own async handlers and awaits
// their promises, so every check runs exactly after the stale response has been processed.
(async () => {
  const results = [];
  const check = (name, cond, detail) => results.push({ name, pass: !!cond, detail: detail ?? null });
  const stopAt = new URLSearchParams(location.search).get("stop");
  const $ = (id) => document.getElementById(id);
  const count = (selector) => document.querySelectorAll(selector).length;
  // Condition polling yields with setTimeout(0): requestAnimationFrame does not advance Chrome's
  // headless virtual time, while timers do. This is not a fixed delay.
  const nextTick = () => new Promise((resolve) => setTimeout(resolve, 0));

  // ---- response gates ------------------------------------------------------------------
  const gates = [];
  function gate(match, { fail = false } = {}) {
    let markRequested, release;
    const g = {
      match, fail, used: false,
      requested: new Promise((r) => { markRequested = r; }),
      released: new Promise((r) => { release = r; }),
    };
    g.markRequested = markRequested;
    g.release = release;
    gates.push(g);
    return g;
  }
  const realFetch = window.fetch.bind(window);
  window.fetch = async (url, opts) => {
    const g = gates.find((item) => !item.used && String(url).includes(item.match));
    if (!g) return realFetch(url, opts);
    g.used = true;
    g.markRequested(String(url));
    const response = await realFetch(url, opts);   // the real server answer, held below
    await g.released;
    if (g.fail) {
      return new Response(JSON.stringify({ detail: "forced late failure (probe)" }), {
        status: 500, headers: { "Content-Type": "application/json" },
      });
    }
    return response;
  };

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
  const mnOverlayCount = () => count(".outage-line, .violation-line, .isolated-bus, .isolated-line, .monitored-line, .mn-synthetic-gen, .mn-monitored-marker");
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
      const gateA = gate("/api/grids/mn-osm-35k/security-analysis?outage_line_id=484333456-1_0");
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
      type("mn-monitored-line", "303374638-1_0");
      const gateS = gate("/api/grids/mn-osm-35k/sensitivity-analysis?");
      const before = snapshot();
      const runS = runSensitivityAnalysis();
      await gateS.requested;
      type("mn-monitored-line", "484276912-1_0");
      gateS.release();
      await runS;
      check("MN Sensitivity(monitored edit): stale ranking not applied", delta(before, "applyMongoliaSensitivityResult") === 0);
      check("MN Sensitivity(monitored edit): no monitored/generator markers", count(".monitored-line, .mn-synthetic-gen, .mn-monitored-marker") === 0);
      check("MN Sensitivity(monitored edit): no ranking text", !sensitivityText().includes("민감도 순위"), sensitivityText().slice(0, 80));
      check("MN Sensitivity(monitored edit): Security overlay kept", outageHighlighted("751261406-1_0"));
      const runNew = runSensitivityAnalysis();
      await runNew;
      check("MN Sensitivity: new request applied once", delta(before, "applyMongoliaSensitivityResult") === 1);
      check("MN Sensitivity: new monitored line highlighted", monitoredHighlighted("484276912-1_0") && !monitoredHighlighted("303374638-1_0"));
      check("MN Sensitivity: generator rank markers drawn", count(".mn-synthetic-gen") > 0 && count(".mn-monitored-marker") === 1);
      check("MN Sensitivity: ranking + FD shown", sensitivityText().includes("민감도 순위") && sensitivityText().includes("유한차분 검증: 통과"));
    })) return finish();

    // ---- 2b. Mongolia Sensitivity: pending, outage line edited --------------------------
    if (await scenario("mn_sensitivity_outage", async () => {
      const gateS = gate("/api/grids/mn-osm-35k/sensitivity-analysis?");
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
      type("mn-monitored-line", "");
      const gateSec = gate("/api/grids/mn-osm-35k/security-analysis?outage_line_id=484333456-1_0");
      const gateSens = gate("/api/grids/mn-osm-35k/sensitivity-analysis?");
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
      check("Switch MN→KPG: KPG panels show defaults", securityText().includes("사고 선로를 입력하고 분석하세요") && !sensitivityText().includes("합성"), securityText());
      check("Switch MN→KPG: KPG default outage restored", $("outage-line").value === "LINE-16-28");
    })) return finish();

    // ---- 3a. KPG Security: A pending, input edited, A arrives -----------------------------
    if (await scenario("kpg_security", async () => {
      const gateA = gate("/api/security-analysis?outage_line_id=LINE-16-28");
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
      const gateOld = gate("/api/sensitivity-analysis?outage_line_id=LINE-16-28", { fail: true });
      const before = snapshot();
      const runOld = runSensitivityAnalysis();
      await gateOld.requested;
      const runNew = runSensitivityAnalysis();
      await runNew;
      const markersAfterNew = count(".sensitivity-marker");
      check("KPG Sensitivity: newer result applied", delta(before, "applySensitivityResult") === 1 && sensitivityText().includes("LINE-16-22"));
      check("KPG Sensitivity: KPG generator markers drawn", markersAfterNew > 0 && count(".mn-synthetic-gen") === 0);
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
      const gateSens = gate("/api/sensitivity-analysis?outage_line_id=LINE-16-28");
      const gateSec = gate("/api/security-analysis?outage_line_id=LINE-16-28");
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
