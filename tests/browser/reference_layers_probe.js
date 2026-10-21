// Browser regression probe: reference data stays out of the UI while Mongolia zoom remains.
// Injected by tests/test_browser_stale_responses.py (?probe=reference).
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
  const switchGrid = async (id, mongolia) => {
    const select = $("grid-select");
    select.value = id;
    select.dispatchEvent(new Event("change"));
    await waitUntil(() => isMongoliaGrid() === mongolia && currentMapData && pfPreview, `grid ${id} loaded`);
    await nextTick();
  };
  const finish = () => {
    const pre = document.createElement("pre");
    pre.id = "__probe_result";
    pre.textContent = JSON.stringify({ results, stoppedAt: stopAt || null });
    document.body.appendChild(pre);
  };
  const noReferenceUi = () => {
    const menu = document.querySelector(".map-layer-control");
    return $("reference-layer-options") === null && $("layer-ref-lines") === null
      && $("layer-ref-substations") === null && !menu.textContent.includes("110kV 추가 송전망")
      && !menu.textContent.includes("추가 변전소");
  };
  const noReferenceSvg = () => count(
    ".reference-layer, .reference-line, .reference-line-hit, .reference-substation, [data-ref-id]"
  ) === 0;
  const referenceApiRequests = () => performance.getEntriesByType("resource")
    .filter((entry) => entry.name.includes("/reference-layers"));

  try {
    await waitUntil(() => currentMapData && pfPreview && !isMongoliaGrid(), "KPG loaded");
    check("KPG: reference toggles and menu are absent", noReferenceUi());
    check("KPG: no reference equipment SVG", noReferenceSvg());
    check("KPG: reference API was not requested automatically", referenceApiRequests().length === 0,
      referenceApiRequests().map((entry) => entry.name));
    const kpgBox = $("network-map").getAttribute("viewBox");
    $("network-map").dispatchEvent(new WheelEvent("wheel", {
      deltaY: -400, clientX: 300, clientY: 300, bubbles: true, cancelable: true
    }));
    check("KPG: wheel does not zoom", $("network-map").getAttribute("viewBox") === kpgBox
      && !$("network-map").classList.contains("map-zoomed"));
    if (stop("kpg")) return finish();

    await switchGrid("mn-osm-35k", true);
    check("MN: reference toggles and menu are absent", noReferenceUi());
    check("MN: reference equipment SVG is not generated", noReferenceSvg());
    check("MN: reference API was not requested automatically", referenceApiRequests().length === 0,
      referenceApiRequests().map((entry) => entry.name));
    check("MN: base map is rendered", count(".network-bus") > 0 && count(".network-line") > 0);
    const defaultViewBox = $("network-map").getAttribute("viewBox");
    if (stop("mn_map")) return finish();

    const base = baseViewBox();
    const projected = mapProjection({ longitude: 106.95, latitude: 47.91 });
    setMapView(projected.x - base[2] / 20, projected.y - base[3] / 20, base[2] / 10);
    check("MN: map zoom works without reference layers",
      $("network-map").classList.contains("map-zoomed") && !$("map-zoom-reset").hidden
      && $("network-map").getAttribute("viewBox") !== defaultViewBox);
    if (stop("mn_zoom")) return finish();
    $("map-zoom-reset").click();
    check("MN: reset restores the full map", $("network-map").getAttribute("viewBox") === defaultViewBox
      && !$("network-map").classList.contains("map-zoomed") && $("map-zoom-reset").hidden);

    await switchGrid("kr-kpg193", false);
    check("Back to KPG: reference UI and SVG remain absent", noReferenceUi() && noReferenceSvg());
    await switchGrid("mn-osm-35k", true);
    check("Back to MN: reference UI and SVG remain absent", noReferenceUi() && noReferenceSvg());
    check("Grid switches never request reference API automatically", referenceApiRequests().length === 0,
      referenceApiRequests().map((entry) => entry.name));
  } catch (error) {
    check("probe exception", false, String(error && error.stack || error));
  }
  finish();
})();
