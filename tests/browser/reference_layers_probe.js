// Browser regression probe: Mongolia reference-only layers (110 kV lines, substations) and the
// Mongolia-only zoom. Injected by tests/test_browser_stale_responses.py (?probe=reference).
// Real API data. `?stop=<name>` ends at a named state for screenshots.
(async () => {
  const results = [];
  const check = (name, cond, detail) => results.push({ name, pass: !!cond, detail: detail ?? null });
  const stopAt = new URLSearchParams(location.search).get("stop");
  const stop = (name) => stopAt === name;
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
  const toggle = (id, on) => { $(id).checked = on; $(id).dispatchEvent(new Event("change")); };
  const switchGrid = async (id, mongolia) => {
    const select = $("grid-select");
    select.value = id;
    select.dispatchEvent(new Event("change"));
    await waitUntil(() => isMongoliaGrid() === mongolia && currentMapData && pfPreview, `grid ${id} loaded`);
    if (mongolia) await waitUntil(() => referenceLayers || referenceLayersError, "reference layers loaded");
  };
  const pickTheme = (value) => document.querySelector(`#theme-switch input[value="${value}"]`).click();
  const norm = (c) => { const s = document.createElementNS("http://www.w3.org/2000/svg", "line"); s.style.stroke = c; $("network-map").appendChild(s); const v = getComputedStyle(s).stroke; s.remove(); return v; };
  const km = (a, b) => {
    const r = Math.PI / 180, dLat = (b[1] - a[1]) * r, dLon = (b[0] - a[0]) * r;
    const h = Math.sin(dLat / 2) ** 2 + Math.cos(a[1] * r) * Math.cos(b[1] * r) * Math.sin(dLon / 2) ** 2;
    return 2 * 6371.0088 * Math.asin(Math.sqrt(h));
  };
  const viewRegion = (lonMin, latMin, lonMax, latMax) => {
    const a = mapProjection({ longitude: lonMin, latitude: latMax }), b = mapProjection({ longitude: lonMax, latitude: latMin });
    const base = baseViewBox();
    const width = Math.max(b.x - a.x, (b.y - a.y) * base[2] / base[3], base[2] / MAX_MAP_ZOOM);
    setMapView((a.x + b.x) / 2 - width / 2, (a.y + b.y) / 2 - width * base[3] / base[2] / 2, width);
  };
  const UB = [106.78, 47.84, 107.12, 47.98];
  const DARKHAN = [105.90, 49.38, 106.01, 49.46];
  const clickCenter = (el) => {
    const box = el.getBoundingClientRect();
    el.dispatchEvent(new MouseEvent("click", { bubbles: true, clientX: box.x + box.width / 2, clientY: box.y + box.height / 2 }));
  };
  const finish = () => {
    const pre = document.createElement("pre");
    pre.id = "__probe_result";
    pre.textContent = JSON.stringify({ results, stoppedAt: stopAt || null,
      counts: referenceLayers ? { lines: referenceLayers.lines.length, substations: referenceLayers.substations.length } : null });
    document.body.appendChild(pre);
  };
  try {
    pickTheme("light");
    await waitUntil(() => currentMapData && pfPreview && !isMongoliaGrid(), "KPG loaded");

    // ---- KPG: no reference layers, no zoom ---------------------------------------------------
    check("KPG: reference toggles hidden + no reference elements", $("reference-layer-options").hidden && count(".reference-layer") === 0);
    const kpgBox = $("network-map").getAttribute("viewBox");
    $("network-map").dispatchEvent(new WheelEvent("wheel", { deltaY: -400, clientX: 300, clientY: 300, bubbles: true, cancelable: true }));
    check("KPG: wheel does not zoom", $("network-map").getAttribute("viewBox") === kpgBox && !$("network-map").classList.contains("map-zoomed"));

    // ---- Mongolia default: OFF and identical to the existing map ------------------------------
    await switchGrid("mn-osm-35k", true);
    check("MN: reference data loaded via separate API", !!referenceLayers && referenceLayers.reference_only === true, referenceLayersError);
    check("MN: toggles shown, labelled, default OFF", !$("reference-layer-options").hidden
      && !$("layer-ref-lines").checked && !$("layer-ref-substations").checked
      && $("layer-ref-lines").parentElement.textContent.includes("110kV 추가 송전망")
      && $("layer-ref-substations").parentElement.textContent.includes("추가 변전소"));
    check("MN OFF: no reference elements drawn", count(".reference-layer, .reference-line, .reference-substation") === 0);
    check("MN OFF: base map counts unchanged (173 buses / 159 lines)", count(".network-bus") === 173 && count(".network-line") === 159);
    const offSvg = $("network-map").innerHTML;
    if (stop("ref_off")) return finish();

    // ---- ON: drawn, separate from model equipment ----------------------------------------------
    toggle("layer-ref-lines", true);
    toggle("layer-ref-substations", true);
    const refLines = [...document.querySelectorAll(".reference-line")];
    const refSubs = [...document.querySelectorAll(".reference-substation")];
    check("MN ON: every reference line drawn", refLines.length === referenceLayers.lines.length && refLines.length > 0, refLines.length);
    check("MN ON: every reference substation drawn", refSubs.length === referenceLayers.substations.length && refSubs.length > 0, refSubs.length);
    check("MN ON: base map counts unchanged", count(".network-bus") === 173 && count(".network-line") === 159);
    check("MN ON: reference lines are not model lines", refLines.every((el) => !el.classList.contains("network-line") && !el.dataset.lineId)
      && count(".network-line[data-ref-id], [data-line-id^='REF-']") === 0);
    check("MN ON: reference lines are straight, thin, dashed", refLines.every((el) => el.tagName === "line"
      && getComputedStyle(el).strokeDasharray !== "none" && parseFloat(getComputedStyle(el).strokeWidth) < 1));
    check("MN ON: base lines stay solid", [...document.querySelectorAll(".network-line")].every((el) => getComputedStyle(el).strokeDasharray === "none"));
    check("MN ON: substations use a separate square symbol", refSubs.every((el) => el.tagName === "rect"));
    const order = [...$("network-map").children];
    check("MN ON: model buses drawn above reference layers",
      order.indexOf(document.querySelector(".reference-substations")) < order.indexOf(document.querySelector(".network-bus"))
      && order.indexOf(document.querySelector(".reference-lines")) < order.indexOf(document.querySelector(".network-bus")));
    // Duplicate minimisation
    const buses = currentMapData.buses.map((b) => [b.longitude, b.latitude]);
    const subsPts = referenceLayers.substations.map((s) => [s.longitude, s.latitude]);
    const minBusKm = Math.min(...subsPts.map((p) => Math.min(...buses.map((b) => km(p, b)))));
    check("Dedup: no reference substation within 1 km of a map bus", minBusKm > 1.0, minBusKm.toFixed(3));
    let minSubPair = Infinity;
    subsPts.forEach((p, i) => subsPts.slice(i + 1).forEach((q) => { minSubPair = Math.min(minSubPair, km(p, q)); }));
    check("Dedup: no two reference substations within 0.2 km", minSubPair > 0.2, minSubPair.toFixed(3));
    const same = (a1, a2, b1, b2, tol) => (km(a1, b1) < tol && km(a2, b2) < tol) || (km(a1, b2) < tol && km(a2, b1) < tol);
    const busById = new Map(currentMapData.buses.map((b) => [b.bus_id, [b.longitude, b.latitude]]));
    const overlapsModel = referenceLayers.lines.filter((r) => currentMapData.lines.some((l) => same(r.from, r.to, busById.get(l.bus1_id), busById.get(l.bus2_id), 0.5)));
    check("Dedup: no reference line coincides with a model line", overlapsModel.length === 0, overlapsModel.map((r) => r.ref_id));
    const refPairs = referenceLayers.lines.filter((r, i) => referenceLayers.lines.slice(i + 1).some((q) => same(r.from, r.to, q.from, q.to, 0.3)));
    check("Dedup: no two coincident reference lines", refPairs.length === 0, refPairs.map((r) => r.ref_id));
    check("MN ON: reference lines keep voltage colour in light theme", refLines.every((el) => getComputedStyle(el).stroke === norm("#2563eb")));
    if (stop("ref_on")) return finish();

    // ---- OFF again: exactly the original SVG ----------------------------------------------------
    toggle("layer-ref-lines", false);
    toggle("layer-ref-substations", false);
    check("MN OFF again: SVG identical to the default map", $("network-map").innerHTML === offSvg);

    // ---- Region comparison views (Mongolia-only zoom), before any analysis -----------------------
    viewRegion(...UB);
    if (stop("ub_off")) return finish();
    toggle("layer-ref-lines", true); toggle("layer-ref-substations", true);
    if (stop("ub_on")) return finish();
    viewRegion(...DARKHAN);
    if (stop("darkhan_on")) return finish();
    toggle("layer-ref-lines", false); toggle("layer-ref-substations", false);
    if (stop("darkhan_off")) return finish();

    // ---- Not selectable for N-1 / Sensitivity -----------------------------------------------------
    toggle("layer-ref-lines", true);
    toggle("layer-ref-substations", true);
    viewRegion(...UB);
    const hit = document.querySelector(".reference-line-hit");
    const securityBefore = $("security-result").innerHTML;
    const securityCollapsed = $("security-analysis-box").classList.contains("collapsed");
    clickCenter(hit);
    check("Ref line click: tooltip only, outage input untouched", $("outage-line").value === ""
      && $("map-tooltip").style.display === "block" && $("map-tooltip-content").textContent.includes("N-1/Sensitivity 대상 아님")
      && document.querySelector(".reference-line.selected-ref") !== null);
    check("Ref line click: Security panel untouched", $("security-result").innerHTML === securityBefore
      && $("security-analysis-box").classList.contains("collapsed") === securityCollapsed);
    if (stop("ub_tooltip")) return finish();
    hideMapTooltip();
    clickCenter(document.querySelector(".reference-substation"));
    check("Ref substation click: tooltip only", $("outage-line").value === "" && $("map-tooltip-content").textContent.includes("참조 변전소"));
    hideMapTooltip();
    const refId = referenceLayers.lines[0].ref_id;
    const secResp = await fetch(`/api/grids/mn-osm-35k/security-analysis?outage_line_id=${encodeURIComponent(refId)}`);
    check("API: reference line rejected as N-1 outage", secResp.status === 404, secResp.status);
    const senResp = await fetch(`/api/grids/mn-osm-35k/sensitivity-analysis?outage_line_id=751261406-1_0&monitored_line_id=${encodeURIComponent(refId)}`);
    const senDetail = (await senResp.json()).detail || "";
    check("API: reference line rejected as monitored line", senResp.status === 422 && senDetail.includes(refId), [senResp.status, senDetail]);

    // Real Security + Sensitivity with layers ON: overlays only on model lines.
    type("outage-line", "237060334-1_0");   // Ulaanbaatar 110 kV (Bus 1–11): overlay next to reference lines
    await runSecurityAnalysis();
    check("Security with layers ON: outage highlight on the model line only",
      count(".network-line.outage-line") === 1 && count(".reference-line.outage-line, .reference-line.violation-line, .reference-line.monitored-line, .reference-line.isolated-line") === 0);
    type("monitored-line", "");
    await runSensitivityAnalysis();
    const options = [...$("monitored-line-options").options].map((o) => o.value);
    check("Sensitivity with layers ON: candidates and monitored-line options exclude reference IDs",
      count(".candidate-marker") > 0 && options.every((v) => !v.startsWith("REF-")) && !$("sensitivity-result").textContent.includes("REF-"), options.length);
    $("pf-display-mode").value = "line_loading"; $("pf-display-mode").dispatchEvent(new Event("change"));
    check("PF line-loading mode: reference lines keep their own colour", refLines.length === count(".reference-line")
      && [...document.querySelectorAll(".reference-line")].every((el) => getComputedStyle(el).stroke === norm("#2563eb")));
    $("pf-display-mode").value = "base"; $("pf-display-mode").dispatchEvent(new Event("change"));
    if (stop("ub_analysis_on")) return finish();
    resetSecurityHighlight(); resetSensitivityMarkers(); updateAnalysisLegend();

    // ---- Zoom (Mongolia only) -------------------------------------------------------------------
    check("Zoom: UB view zoomed with reset button", $("network-map").classList.contains("map-zoomed") && !$("map-zoom-reset").hidden && mapView.zoom > 5, mapView);
    const bus = document.querySelector(".network-bus");
    check("Zoom: bus markers keep their screen size", Math.abs(bus.getBoundingClientRect().width - 2 * 4 * $("network-map").getScreenCTM().a / mapView.zoom) < 1.5,
      bus.getBoundingClientRect().width);
    $("map-zoom-reset").click();
    check("Zoom reset: original viewBox restored", $("network-map").getAttribute("viewBox") === "0 0 1200 650"
      && !$("network-map").classList.contains("map-zoomed") && $("map-zoom-reset").hidden);

    // ---- Dark theme ----------------------------------------------------------------------------
    pickTheme("dark");
    check("Dark: reference lines redrawn with the dark tint", [...document.querySelectorAll(".reference-line")].every((el) => getComputedStyle(el).stroke === norm("#60a5fa")));
    check("Dark: reference substations filled with the dark surface",
      getComputedStyle(document.querySelector(".reference-substation")).fill === norm(getComputedStyle(document.documentElement).getPropertyValue("--surface").trim()));
    check("Dark: toggles still on after theme change", $("layer-ref-lines").checked && count(".reference-line") === referenceLayers.lines.length);
    if (stop("national_on_dark")) return finish();
    viewRegion(...UB);
    if (stop("ub_on_dark")) return finish();
    pickTheme("light");

    // ---- Grid switches: KPG unaffected, Mongolia back to default OFF ------------------------------
    await switchGrid("kr-kpg193", false);
    check("Back to KPG: no reference elements, toggles hidden, no zoom", count(".reference-layer") === 0 && $("reference-layer-options").hidden
      && !$("network-map").classList.contains("map-zoomed"));
    await switchGrid("mn-osm-35k", true);
    check("Back to MN: layers default OFF again", !$("layer-ref-lines").checked && !$("layer-ref-substations").checked
      && count(".reference-layer") === 0 && $("network-map").innerHTML === offSvg);
  } catch (error) {
    check("probe exception", false, String(error && error.stack || error));
  }
  finish();
})();
