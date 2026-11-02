/* K-EMS console bridge.
 *
 * app.js is the Artifact demo, kept byte-identical. This classic script runs after it in the same
 * global scope and adds the EMS API grids from GET /api/grids beside the demo grids, wrapping
 * app.js functions the same way app.js wraps its own (const _f=f; f=function(){...}).
 *
 * Phase 2: an EMS API grid is shown inside the Artifact's own 관제 layout. The map comes from
 * GET /api/grids/{id}/map (drawn by api-map.js; Mongolia lines use the verified display geometry when
 * it validates), and the KPI tiles, line colours and the side panel come from
 * GET /api/grids/{id}/pf-preview where the catalog says it exists. Only values present in those
 * responses are shown; anything else is "N/A"/"미지원". Every grid switch aborts the previous
 * requests and late responses are dropped. While an API grid is selected the Artifact DC engine,
 * the demo map, KPIs, chat and screens stay idle and hidden, and an API failure never falls back to
 * demo numbers. Analysis (N-1, sensitivity) and the assistant are not wired yet (Phase 3-4).
 * Everything lives in this IIFE so no app.js global lexical name can collide.
 */
(() => {
  'use strict';

  const API_PREFIX = 'api:';
  const CATALOG_URL = '/api/grids';
  const TIMEOUT_MS = {catalog: 10000, map: 30000, geometry: 15000, pf: 20000};
  // Display-only GIS routes exist for this grid only (GET /api/grids/mn-osm-35k/display-geometry).
  const DISPLAY_GEOMETRY = {'mn-osm-35k': '/api/grids/mn-osm-35k/display-geometry'};
  // Short display names only; grid IDs and capabilities always come from GET /api/grids.
  const API_ALIASES = {'kr-kpg193': 'KPG-193', 'mn-osm-35k': '몽골 OSM', 'kr-osm-transmission': '한국 OSM'};
  const DEMO_LABELS = {kpg193: 'KPG193 (DC 데모)', ieee39: 'IEEE39 (데모)', ieee14: 'IEEE14 (데모)', six: '가상 6모선 (데모)'};
  // A flag missing from the response is shown as missing, never assumed.
  const CAPABILITIES = [
    ['map_available', '지도', '연동'],
    ['pf_preview_available', 'PF 미리보기', '연동 (조회 전용)'],
    ['security_analysis_available', '선로 N-1 Security Analysis', '미연동'],
    ['sensitivity_analysis_available', 'Sensitivity Analysis', '미연동'],
    ['generator_contingency_available', '발전기 탈락 해석', '미연동'],
    ['chat_available', 'AI 채팅', '미연동'],
    ['analysis_available', 'KPG 해석 묶음 (호환 플래그)', '미연동'],
  ];
  const CAPABILITY_NOTES = {security_analysis_available: 'security_analysis_note', sensitivity_analysis_available: 'sensitivity_analysis_note'};
  // Demo screen renders app.js calls by name (journal, live feed, lake); idle while an API grid is shown.
  const DEMO_RENDERS = ['renderReport', 'dashRender', 'bmRender', 'kpiRender', 'snRender', 'lakeRender', 'liveSrcRender', 'gwRender', 'kbRender'];

  const select = document.getElementById('model');
  const demoChange = select.onchange;  // app.js: loadModel($('model').value)
  const state = {
    source: 'demo',     // 'demo' | 'api'
    gridId: null,       // selected EMS API grid
    lastDemo: M.id,     // demo grid to return to
    token: 0,           // bumped on every grid selection; an older pending switch is abandoned
    catalog: null,      // last successful, validated /api/grids
    catalogError: null,
    checkedAt: null,
    view: null,         // everything shown for the selected API grid (replaced on every switch)
  };

  /* ---------- small helpers (text only, never innerHTML with API data) ---------- */
  function h(tag, attrs, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value === null || value === undefined || value === false) continue;
      if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value === true ? '' : String(value));
    }
    node.append(...children.flat(Infinity).filter((child) => child !== null && child !== undefined && child !== false));
    return node;
  }
  const clock = (date) => (date ? [date.getHours(), date.getMinutes(), date.getSeconds()].map((n) => String(n).padStart(2, '0')).join(':') : '-');
  const finite = (value) => typeof value === 'number' && Number.isFinite(value);
  const num = (value, digits) => value.toLocaleString('ko-KR', {minimumFractionDigits: digits, maximumFractionDigits: digits});
  const fmtMW = (value) => `${num(value, Math.abs(value) >= 100 ? 0 : 1)} MW`;
  const fmtPu = (value) => num(value, 3);
  const fmtPct = (value) => `${num(value, 1)}%`;
  const kvText = (kv) => (finite(kv) ? `${num(kv, 0)} kV` : '전압 정보 없음');
  const enc = encodeURIComponent;

  /* ---------- HTTP ---------- */
  class ApiError extends Error {}
  class Aborted extends Error {}

  async function fetchJson(url, timeoutMs, outer) {
    const controller = new AbortController();
    const abort = () => controller.abort();
    if (outer) outer.addEventListener('abort', abort);
    let timedOut = false;
    const timer = setTimeout(() => { timedOut = true; controller.abort(); }, timeoutMs);
    try {
      const response = await fetch(url, {signal: controller.signal, cache: 'no-store', headers: {Accept: 'application/json'}});
      const text = await response.text();
      let payload = null;
      try { payload = JSON.parse(text); } catch (error) { payload = null; }
      if (!response.ok) {
        const detail = payload && typeof payload.detail === 'string' ? ` · ${payload.detail}` : '';
        throw new ApiError(`HTTP ${response.status}${detail}`);
      }
      if (payload === null) throw new ApiError('응답 형식 오류 (JSON 아님)');
      return payload;
    } catch (error) {
      if (error instanceof ApiError) throw error;
      if (outer && outer.aborted) throw new Aborted('grid switched');
      throw new ApiError(timedOut ? `응답 시간 초과 (${timeoutMs / 1000}초)` : 'EMS API 서버에 연결할 수 없습니다');
    } finally {
      clearTimeout(timer);
      if (outer) outer.removeEventListener('abort', abort);
    }
  }
  const settle = (promise) => promise.then((value) => ({ok: true, value}), (error) => ({ok: false, error}));

  /* ---------- EMS API catalog ---------- */
  function validateCatalog(payload) {
    if (!payload || !Array.isArray(payload.grids)) throw new ApiError('응답 형식 오류 (grids 없음)');
    const grids = [];
    const seen = new Set();
    for (const entry of payload.grids) {
      if (!entry || typeof entry !== 'object' || typeof entry.id !== 'string' || !entry.id || seen.has(entry.id)) continue;
      seen.add(entry.id);
      grids.push(entry);
    }
    if (!grids.length) throw new ApiError('응답에 계통이 없습니다');
    return {grids, skipped: payload.grids.length - grids.length};
  }

  let catalogSeq = 0;
  /** Refresh the catalog; only the newest request is applied. Returns null when superseded. */
  async function refreshCatalog() {
    const seq = ++catalogSeq;
    let catalog = null;
    let error = null;
    try { catalog = validateCatalog(await fetchJson(CATALOG_URL, TIMEOUT_MS.catalog)); } catch (failure) { error = failure.message; }
    if (seq !== catalogSeq) return null;
    if (catalog) {
      state.catalog = catalog;
      state.catalogError = null;
      state.checkedAt = new Date();
    } else {
      state.catalogError = error;
    }
    render();
    return {catalog, error};
  }

  const entryFlag = (entry, flag) => (entry && typeof entry[flag] === 'boolean' ? entry[flag] : null);
  const availableFlags = (entry) => Object.keys(entry).filter((key) => key.endsWith('_available'));
  // "지도 전용" only when every known capability is present and false except the map.
  const mapOnly = (entry) => entry.map_available === true
    && CAPABILITIES.every(([flag]) => flag === 'map_available' || entry[flag] === false)
    && availableFlags(entry).every((key) => key === 'map_available' || entry[key] !== true);
  const apiName = (entry, id) => (entry && typeof entry.name === 'string' && entry.name) || API_ALIASES[id] || id;
  const apiLabel = (entry) => `${API_ALIASES[entry.id] || apiName(entry, entry.id)} (EMS API${mapOnly(entry) ? ', 지도 전용' : ''})`;
  const cachedEntry = (id) => (state.catalog ? state.catalog.grids.find((grid) => grid.id === id) : null) || null;
  const currentValue = () => (state.source === 'api' ? API_PREFIX + state.gridId : state.lastDemo);

  /* ---------- grid select: EMS API group + Artifact demo group ---------- */
  const apiGroup = h('optgroup', {label: 'EMS API 계통'});
  const demoGroup = h('optgroup', {label: 'Artifact 데모 계통'});
  for (const option of [...select.options]) {
    if (DEMO_LABELS[option.value]) option.textContent = DEMO_LABELS[option.value];
    demoGroup.append(option);
  }
  select.append(apiGroup, demoGroup);
  select.value = state.lastDemo;
  select.title = '계통 선택 · EMS API 계통과 Artifact 데모 계통';

  // With an API grid on screen a retry reloads that grid's whole view, not just the list.
  const retry = h('button', {type: 'button', class: 'btn kems-retry', hidden: true,
    onclick: () => (state.source === 'api' ? openView(state.gridId) : refreshCatalog())}, 'EMS API 다시 연결');
  select.closest('label').after(retry);

  function renderOptions() {
    const options = [];
    if (state.catalog) {
      for (const entry of state.catalog.grids) options.push(new Option(apiLabel(entry), API_PREFIX + entry.id));
    } else {
      const option = new Option(state.catalogError ? `EMS API 목록을 불러오지 못함 · ${state.catalogError}` : 'EMS API 목록 확인 중…', '');
      option.disabled = true;
      options.push(option);
    }
    // The selected API grid stays listed even if a refresh dropped it, so the select never shows another grid.
    if (state.source === 'api' && !options.some((option) => option.value === API_PREFIX + state.gridId)) {
      options.push(new Option(`${state.gridId} (EMS API 목록에 없음)`, API_PREFIX + state.gridId));
    }
    apiGroup.replaceChildren(...options);
    select.value = currentValue();
    retry.hidden = !state.catalogError;
    retry.title = state.catalogError ? `EMS API 목록 오류: ${state.catalogError}` : '';
  }

  /* ---------- demo isolation ---------- */
  function demoBlocker() {
    if (busy) return '데모 응답 처리';
    if (TRN.on) return '훈련 모드';
    if (['runBench', 'runQB', 'runReg'].some((id) => document.getElementById(id).disabled)) return '성능 평가 채점';
    return null;
  }

  // Stop every demo loop that would keep running the DC engine behind the API view.
  function quiesceDemo() {
    stopPlay();
    tlClose();
    if (LK.on) lkToggle();
    drawerClose();
    const alarms = document.getElementById('almPop');
    if (alarms) alarms.hidden = true;
  }

  let loadController = null;
  async function selectApiGrid(gridId, token) {
    const blocker = demoBlocker();
    if (blocker) {
      toast(`${blocker} 중에는 EMS API 계통으로 전환할 수 없습니다. 끝난 뒤 다시 선택하세요.`);
      select.value = currentValue();
      return;
    }
    if (state.source === 'demo') {
      if (MODE === 'live') {
        await switchMode('pub');  // ends the simulated live feed and restores the public demo grid
        if (token !== state.token) return;
      }
      state.lastDemo = M.id;
      quiesceDemo();
    }
    state.source = 'api';
    state.gridId = gridId;
    openView(gridId);
  }

  /** A new view for the grid: aborts the previous view's requests and clears its map and values. */
  function openView(gridId) {
    if (loadController) loadController.abort();
    loadController = new AbortController();
    const view = {
      gridId,
      status: 'loading',            // catalog check: loading | ready | error | missing
      entry: cachedEntry(gridId),
      error: null,
      map: {status: 'idle'},        // idle | loading | ready | error | unavailable
      geometry: {status: 'none'},   // none | loading | ready | error | invalid
      pf: {status: 'idle'},         // idle | loading | ready | nonconverged | invalid | error | blocked | unsupported | unknown
      selection: null,
      mode: 'voltage',
    };
    state.view = view;
    apiMap.clear();
    closeCard();
    render();
    loadGrid(view, loadController.signal);
  }

  function leaveApi() {
    if (loadController) loadController.abort();
    loadController = null;
    state.source = 'demo';
    state.gridId = null;
    state.view = null;
    apiMap.clear();
    closeCard();
    render();  // drops every API value from the DOM before the demo grid loads
  }

  const navBar = document.querySelector('nav');
  select.onchange = () => {
    const value = select.value;
    const token = ++state.token;
    if (value.startsWith(API_PREFIX)) {
      selectApiGrid(value.slice(API_PREFIX.length), token);
      return;
    }
    state.lastDemo = value;  // before leaveApi(): its render() restores the select to lastDemo
    const wasApi = state.source === 'api';
    if (wasApi) leaveApi();
    demoChange.call(select);
    if (wasApi) {
      // Demo screens were not rendered while the API grid was shown: refresh the open one as a tab click does.
      const active = navBar.querySelector('button[data-v][aria-selected="true"]');
      if (active && active.dataset.v !== 'ops') active.click();
    }
  };

  // app.js binds demo screen renders (DC engine) to tab clicks by reference, so they cannot be wrapped.
  // With an API grid selected the click stops here: only the tab switches, as app.js's own handler does.
  navBar.addEventListener('click', (event) => {
    const button = state.source === 'api' && event.target.closest('button[data-v]');
    if (!button) return;
    event.stopPropagation();
    for (const other of navBar.querySelectorAll('button')) other.setAttribute('aria-selected', other === button);
    for (const view of document.querySelectorAll('.view')) view.classList.toggle('on', view.id === `v-${button.dataset.v}`);
    document.documentElement.classList.toggle('opsfit', button.dataset.v === 'ops');
    opsFit();
    try { history.replaceState(null, '', `#${button.dataset.v}`); } catch (error) { /* history unavailable */ }
  }, true);

  function backToDemo() {
    select.value = state.lastDemo;
    select.dispatchEvent(new Event('change'));
  }

  // Demo entry points are inert while an API grid is selected.
  const demoAsk = ask;
  ask = function (...args) {
    if (state.source === 'api') return Promise.resolve();
    return demoAsk.apply(this, args);
  };
  const demoSwitchMode = switchMode;
  switchMode = async function (...args) {
    if (state.source === 'api') return undefined;
    return demoSwitchMode.apply(this, args);
  };
  // e.g. a role change on the (hidden) 보고서 tab calls addJN() -> renderReport() -> dcpf().
  for (const name of DEMO_RENDERS) {
    const original = window[name];
    if (typeof original !== 'function') continue;
    window[name] = function (...args) {
      if (state.source === 'api') return undefined;
      return original.apply(this, args);
    };
  }

  /* ---------- validation of the map, display geometry and PF responses ---------- */
  const idOf = (value) => (value === null || value === undefined ? '' : String(value));

  function validateMap(map, gridId) {
    if (!map || typeof map !== 'object' || !Array.isArray(map.buses) || !map.buses.length || !Array.isArray(map.lines)) {
      throw new ApiError('지도 응답 형식 오류 (buses/lines 없음)');
    }
    if (typeof map.grid_id === 'string' && map.grid_id !== gridId) throw new ApiError(`지도 응답의 계통 ID(${map.grid_id})가 선택한 계통과 다릅니다`);
    const unique = (items, key, label) => {
      const seen = new Set();
      for (const item of items || []) {
        const id = idOf(item && item[key]);
        if (!id || seen.has(id)) throw new ApiError(`지도 ${label} ID가 비어 있거나 중복됩니다`);
        seen.add(id);
      }
    };
    unique(map.buses, 'bus_id', '모선');
    unique(map.lines, 'line_id', '선로');
    unique(map.transformers, 'transformer_id', '변압기');
    for (const bus of map.buses) {
      if (!finite(bus.latitude) || !finite(bus.longitude) || Math.abs(bus.latitude) > 90 || Math.abs(bus.longitude) > 180) {
        throw new ApiError(`모선 ${idOf(bus.bus_id)}의 좌표가 올바르지 않습니다`);
      }
    }
  }

  function checkGeometry(geometry, gridId, map) {
    if (!geometry || geometry.grid_id !== gridId || geometry.display_only !== true || geometry.connectors !== 'omitted' || !Array.isArray(geometry.lines)) {
      return {status: 'invalid', error: 'GIS 표시 자료 검증 실패 (계통 ID·display_only·connectors)'};
    }
    const mapLines = new Map(map.lines.map((line) => [idOf(line.line_id), line]));
    const routes = new Map();
    let parts = 0;
    let invalid = 0;
    for (const line of geometry.lines) {
      const mapped = line && mapLines.get(idOf(line.line_id));
      const ok = mapped && idOf(line.bus1_id) === idOf(mapped.bus1_id) && idOf(line.bus2_id) === idOf(mapped.bus2_id)
        && Array.isArray(line.osm_parts) && line.osm_parts.length
        && line.osm_parts.every((part) => Array.isArray(part) && part.length >= 2
          && part.every((pt) => Array.isArray(pt) && finite(pt[0]) && finite(pt[1])));
      if (!ok || routes.has(idOf(line.line_id))) { invalid += 1; continue; }
      routes.set(idOf(line.line_id), line.osm_parts);
      parts += line.osm_parts.length;
    }
    return {status: 'ready', routes, parts, invalid, excluded: Array.isArray(geometry.excluded) ? geometry.excluded.length : null};
  }

  function validatePf(pf, gridId, map) {
    if (!pf || typeof pf !== 'object' || !pf.metadata || typeof pf.metadata !== 'object' || !pf.summary || typeof pf.summary !== 'object'
      || !Array.isArray(pf.buses) || !Array.isArray(pf.lines)) {
      throw new ApiError('PF 응답 형식 오류 (metadata/summary/buses/lines)');
    }
    const meta = pf.metadata;
    if (meta.grid_id !== gridId) throw new ApiError(`PF 응답의 계통 ID(${meta.grid_id})가 선택한 계통(${gridId})과 다릅니다`);
    const unique = (items, key, label) => {
      const result = new Map();
      for (const item of items || []) {
        const id = idOf(item && item[key]);
        if (!id || result.has(id)) throw new ApiError(`PF ${label} ID가 비어 있거나 중복됩니다`);
        result.set(id, item);
      }
      return result;
    };
    const buses = unique(pf.buses, 'bus_id', '모선');
    const lines = unique(pf.lines, 'line_id', '선로');
    const transformers = unique(pf.transformers, 'transformer_id', '변압기');
    for (const [key, items] of [['analysis_bus_count', buses], ['analysis_line_count', lines], ['analysis_transformer_count', transformers]]) {
      if (finite(meta[key]) && meta[key] !== items.size) throw new ApiError(`PF ${key}(${meta[key]})와 결과 수(${items.size})가 다릅니다`);
    }
    const mapBuses = new Map(map.buses.map((bus) => [idOf(bus.bus_id), bus]));
    const mapLines = new Map(map.lines.map((line) => [idOf(line.line_id), line]));
    const mapTrafos = new Map((map.transformers || []).map((trafo) => [idOf(trafo.transformer_id), trafo]));
    for (const [id, bus] of buses) {
      const mapped = mapBuses.get(id);
      if (!mapped) throw new ApiError(`PF 모선 ${id}가 지도에 없습니다`);
      if (finite(bus.voltage_kv) && finite(mapped.voltage_kv) && bus.voltage_kv !== mapped.voltage_kv) throw new ApiError(`PF 모선 ${id}의 전압 등급이 지도와 다릅니다`);
    }
    for (const [id, line] of lines) {
      const mapped = mapLines.get(id);
      if (!mapped || idOf(line.bus1_id) !== idOf(mapped.bus1_id) || idOf(line.bus2_id) !== idOf(mapped.bus2_id)) throw new ApiError(`PF 선로 ${id}가 지도와 일치하지 않습니다`);
      if (line.loading_percent != null && (!finite(line.loading_percent) || !(line.rating_mva > 0))) throw new ApiError(`PF 선로 ${id}의 정격 또는 부하율이 올바르지 않습니다`);
    }
    for (const [id, trafo] of transformers) {
      const mapped = mapTrafos.get(id);
      if (!mapped || idOf(trafo.bus1_id) !== idOf(mapped.bus1_id) || idOf(trafo.bus2_id) !== idOf(mapped.bus2_id)) throw new ApiError(`PF 변압기 ${id}가 지도와 일치하지 않습니다`);
    }
    const excluded = {buses: new Set(), lines: new Set()};
    for (const [key, target, mapped, results] of [['excluded_bus_ids', excluded.buses, mapBuses, buses], ['excluded_line_ids', excluded.lines, mapLines, lines]]) {
      if (meta[key] === undefined) continue;
      if (!Array.isArray(meta[key])) throw new ApiError(`PF ${key} 형식 오류`);
      for (const raw of meta[key]) {
        const id = idOf(raw);
        if (!mapped.has(id) || results.has(id)) throw new ApiError(`PF ${key}의 ${id}가 지도·결과와 맞지 않습니다`);
        target.add(id);
      }
    }
    const missing = {
      buses: [...mapBuses.keys()].filter((id) => !buses.has(id) && !excluded.buses.has(id)).length,
      lines: [...mapLines.keys()].filter((id) => !lines.has(id) && !excluded.lines.has(id)).length,
    };
    const converged = meta.pf_status === 'AC_PF_CONVERGED' && pf.summary.pf_converged === true;
    return {meta, summary: pf.summary, buses, lines, transformers, excluded, missing, converged};
  }

  /* ---------- normalized map model for api-map.js ---------- */
  function buildModel(map, geometry) {
    const kvs = [...new Set([...map.buses, ...map.lines].map((item) => item.voltage_kv).filter(finite))].sort((a, b) => b - a);
    const kvClass = (kv) => (finite(kv) ? `k-v${Math.min(3, kvs.indexOf(kv) + 1)}` : 'k-vu');
    const buses = map.buses.map((bus) => ({
      id: idOf(bus.bus_id), name: bus.name_korean || '', nameEn: bus.name_english || '', kv: finite(bus.voltage_kv) ? bus.voltage_kv : null,
      cls: kvClass(bus.voltage_kv), lon: bus.longitude, lat: bus.latitude, raw: bus, gens: [], plants: [], degree: 0, feeds: false,
    }));
    const busById = new Map(buses.map((bus) => [bus.id, bus]));
    for (const gen of map.generators || []) {
      const bus = busById.get(idOf(gen.bus_id));
      if (bus) { bus.gens.push(gen); bus.feeds = true; }
    }
    const plants = [];
    for (const plant of map.powerplants || []) {
      if (!finite(plant.latitude) || !finite(plant.longitude)) continue;
      plants.push({id: idOf(plant.record_id), name: plant.name || '', lon: plant.longitude, lat: plant.latitude, raw: plant});
      const bus = busById.get(idOf(plant.mapped_bus_id));
      if (bus) { bus.plants.push(plant); bus.feeds = true; }
    }
    const routes = geometry && geometry.status === 'ready' ? geometry.routes : null;
    const lines = [];
    let missingEnds = 0;
    for (const line of map.lines) {
      const from = busById.get(idOf(line.bus1_id));
      const to = busById.get(idOf(line.bus2_id));
      if (!from || !to) { missingEnds += 1; continue; }
      from.degree += 1;
      to.degree += 1;
      const id = idOf(line.line_id);
      let parts;
      let source;
      if (routes && routes.has(id)) { parts = routes.get(id); source = 'gis'; }
      else if (Array.isArray(line.coordinates) && line.coordinates.length > 1
        && line.coordinates.every((pt) => Array.isArray(pt) && finite(pt[0]) && finite(pt[1]))) { parts = [line.coordinates]; source = 'route'; }
      else { parts = [[[from.lon, from.lat], [to.lon, to.lat]]]; source = routes ? 'straight-fallback' : 'straight'; }
      lines.push({id, from, to, kv: finite(line.voltage_kv) ? line.voltage_kv : null, cls: kvClass(line.voltage_kv), parts, source, raw: line});
    }
    const transformers = [];
    for (const trafo of map.transformers || []) {
      const from = busById.get(idOf(trafo.bus1_id));
      const to = busById.get(idOf(trafo.bus2_id));
      if (from && to) transformers.push({id: idOf(trafo.transformer_id), from, to, raw: trafo});
    }
    return {
      buses, lines, transformers, plants, busById, kvs, missingEnds,
      country: Array.isArray(map.country_polygons) ? map.country_polygons : [],
      regions: Array.isArray(map.admin_regions) ? map.admin_regions.filter((region) => region && Array.isArray(region.polygons)) : [],
      gis: lines.filter((line) => line.source === 'gis').length,
      straightFallback: lines.filter((line) => line.source === 'straight-fallback').length,
    };
  }

  /* ---------- loading an API grid ---------- */
  async function loadGrid(view, signal) {
    const current = () => state.source === 'api' && state.view === view && !signal.aborted;
    const result = await refreshCatalog();
    if (!current() || result === null) return;
    if (!result.catalog) {
      view.status = 'error';
      view.error = result.error;
      render();
      return;
    }
    const entry = result.catalog.grids.find((grid) => grid.id === view.gridId) || null;
    view.entry = entry;
    view.status = entry ? 'ready' : 'missing';
    if (!entry) { render(); return; }

    const id = view.gridId;
    const mapWanted = entryFlag(entry, 'map_available') === true;
    const pfFlag = entryFlag(entry, 'pf_preview_available');
    view.map = mapWanted ? {status: 'loading'} : {status: 'unavailable'};
    view.pf = pfFlag === true ? {status: 'loading'} : {status: pfFlag === false ? 'unsupported' : 'unknown'};
    view.geometry = mapWanted && DISPLAY_GEOMETRY[id] ? {status: 'loading'} : {status: 'none'};
    render();
    if (!mapWanted) return;

    const mapRequest = settle(fetchJson(`/api/grids/${enc(id)}/map`, TIMEOUT_MS.map, signal));
    const geometryRequest = view.geometry.status === 'loading' ? settle(fetchJson(DISPLAY_GEOMETRY[id], TIMEOUT_MS.geometry, signal)) : null;
    const pfRequest = pfFlag === true ? settle(fetchJson(`/api/grids/${enc(id)}/pf-preview`, TIMEOUT_MS.pf, signal)) : null;

    const mapResult = await mapRequest;
    if (!current()) return;
    let map = null;
    try {
      if (!mapResult.ok) throw mapResult.error;
      validateMap(mapResult.value, id);
      map = mapResult.value;
    } catch (error) {
      view.map = {status: 'error', error: error.message};
      if (view.geometry.status === 'loading') view.geometry = {status: 'none'};
      if (pfRequest) view.pf = {status: 'blocked', error: '지도 오류로 PF 결과를 지도와 대조할 수 없어 표시하지 않습니다'};
      render();
      return;
    }
    if (geometryRequest) {
      const geometryResult = await geometryRequest;
      if (!current()) return;
      view.geometry = geometryResult.ok ? checkGeometry(geometryResult.value, id, map)
        : {status: 'error', error: geometryResult.error.message};
    }
    const model = buildModel(map, view.geometry);
    view.map = {status: 'ready', data: map, model};
    apiMap.show(model);
    paintMap();
    render();

    if (!pfRequest) return;
    const pfResult = await pfRequest;
    if (!current()) return;
    if (!pfResult.ok) {
      view.pf = {status: 'error', error: pfResult.error.message};
    } else {
      try {
        const pf = validatePf(pfResult.value, id, map);
        view.pf = {status: pf.converged ? 'ready' : 'nonconverged', ...pf};
      } catch (error) {
        view.pf = {status: 'invalid', error: error.message};
      }
    }
    if (view.pf.status === 'ready') view.mode = 'loading';
    paintMap();
    render();
    if (view.selection) apiMap.select(view.selection.kind, view.selection.id, false);  // refresh an open card with PF values
  }

  /* ---------- map colours from the API values ---------- */
  function lineBucket(line, pf) {
    if (pf.excluded.lines.has(line.id)) return 'k-ld-none';
    const result = pf.lines.get(line.id);
    if (!result || result.analysis_status !== 'ANALYZED' || !finite(result.loading_percent)) return 'k-ld-none';
    if (result.thermal_violation === true || result.loading_percent >= 100) return 'k-ld-over';
    if (result.loading_percent >= 80) return 'k-ld-high';
    return 'k-ld-ok';
  }

  function paintMap() {
    const view = state.view;
    if (!view || view.map.status !== 'ready') return;
    const pf = view.pf.status === 'ready' ? view.pf : null;
    const loading = pf && view.mode === 'loading';
    apiMap.paint(
      (line) => [line.cls, line.source === 'straight-fallback' ? 'k-straight' : '', loading ? lineBucket(line, pf) : ''].filter(Boolean).join(' '),
      (bus) => Boolean(loading && pf.buses.get(bus.id) && pf.buses.get(bus.id).voltage_violation === true),
    );
  }

  /* ---------- DOM inside the Artifact 관제 layout ---------- */
  const ops = document.getElementById('v-ops');
  const mapPanel = ops.querySelector('.mapwrap').closest('.panel');
  const mapWrap = mapPanel.querySelector('.mapwrap');
  const chatPanel = ops.querySelector('.panel.chat');

  const descBox = h('div', {class: 'kems-only kems-desc', id: 'kemsDesc'});
  document.getElementById('modelDesc').after(descBox);

  const modeToggle = h('span', {class: 'lvtog kems-only', id: 'kemsMode', role: 'group', 'aria-label': '지도 표시'},
    h('button', {type: 'button', 'data-mode': 'voltage', onclick: () => setMode('voltage')}, '전압 등급'),
    h('button', {type: 'button', 'data-mode': 'loading', onclick: () => setMode('loading')}, 'PF 부하율'));
  mapPanel.querySelector('.ph .zoom').before(modeToggle);

  const mapHost = h('div', {class: 'kems-only kems-maphost', id: 'kemsMapHost'});
  const mapMessage = h('div', {class: 'kems-mapmsg', id: 'kemsMapMsg', role: 'status'});
  const searchInput = h('input', {id: 'kemsQ', placeholder: '설비 검색 · 모선 · 선로 · 발전소 ID (/)', autocomplete: 'off', 'aria-label': 'EMS API 계통 설비 검색'});
  const searchResults = h('div', {class: 'msres', id: 'kemsRes', hidden: true});
  const card = h('div', {class: 'mapcard kems-card', id: 'kemsCard', hidden: true});
  mapHost.append(mapMessage, h('div', {class: 'msbox kems-search'}, searchInput, searchResults), card);
  mapWrap.append(mapHost);
  const apiMap = KemsApiMap.create(mapHost, {onSelect: (hit) => showCard(hit)});

  const legend = h('div', {class: 'legend kems-only', id: 'kemsLegend'});
  document.getElementById('legend').after(legend);
  const kpis = h('div', {class: 'kpis kems-kpis kems-only', id: 'kemsKpis'});
  mapPanel.querySelector('.kpis').after(kpis);

  const side = h('div', {class: 'kems-only kems-side', id: 'kemsSide', 'aria-live': 'polite'});
  chatPanel.querySelector('#msgs').before(side);

  // The Artifact zoom buttons drive the API map while an API grid is shown (their own handlers move the demo map).
  mapPanel.querySelector('.ph .zoom').addEventListener('click', (event) => {
    const button = state.source === 'api' && event.target.closest('button');
    if (!button) return;
    event.stopPropagation();
    if (button.id === 'zin') apiMap.zoomBy(0.7);
    else if (button.id === 'zout') apiMap.zoomBy(1 / 0.7);
    else if (button.id === 'zfit') apiMap.fit();
  }, true);
  document.addEventListener('keydown', (event) => {
    if (state.source !== 'api' || event.key !== '/' || /INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName) || !ops.classList.contains('on')) return;
    event.preventDefault();
    event.stopPropagation();
    searchInput.focus();
  }, true);

  function setMode(mode) {
    const view = state.view;
    if (!view || (mode === 'loading' && view.pf.status !== 'ready')) return;
    view.mode = mode;
    paintMap();
    render();
  }

  /* ---------- search and selection card ---------- */
  const KIND_LABEL = {bus: '모선', line: '선로', plant: '발전소', transformer: '변압기'};
  const busLabel = (bus) => (bus.name && bus.name !== bus.id ? `${bus.name} (${bus.id})` : bus.id);
  let searchHits = [];
  searchInput.addEventListener('input', () => {
    searchHits = apiMap.search(searchInput.value);
    searchResults.replaceChildren(...searchHits.map((hit, i) => h('button', {type: 'button', class: 'msr', 'data-i': i,
      onmousedown: (event) => { event.preventDefault(); pickSearch(i); }},
      h('b', {}, `${KIND_LABEL[hit.kind]} ${hit.kind === 'bus' ? busLabel(hit.item) : hit.item.id}`),
      h('span', {}, hit.kind === 'line' ? `${busLabel(hit.item.from)} — ${busLabel(hit.item.to)}` : hit.kind === 'plant' ? hit.item.name : kvText(hit.item.kv)))));
    searchResults.hidden = !searchHits.length;
  });
  searchInput.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && searchHits.length) pickSearch(0);
    if (event.key === 'Escape') { searchResults.hidden = true; searchInput.blur(); }
  });
  searchInput.addEventListener('blur', () => setTimeout(() => { searchResults.hidden = true; }, 150));
  function pickSearch(i) {
    const hit = searchHits[i];
    if (!hit) return;
    searchResults.hidden = true;
    apiMap.select(hit.kind, hit.item.id, true);
  }

  function closeCard() {
    card.hidden = true;
    card.replaceChildren();
    if (state.view) state.view.selection = null;
  }

  const row = (key, value) => (value === null || value === undefined || value === '' ? null : h('div', {class: 'kems-kv'}, h('span', {}, key), h('b', {}, String(value))));

  function showCard(hit) {
    const view = state.view;
    if (!view || !hit) { closeCard(); return; }
    view.selection = {kind: hit.kind, id: hit.item.id};
    const pf = view.pf.status === 'ready' || view.pf.status === 'nonconverged' ? view.pf : null;
    const item = hit.item;
    const rows = [];
    let title = `${KIND_LABEL[hit.kind]} ${hit.kind === 'bus' ? busLabel(item) : item.id}`;
    if (hit.kind === 'bus') {
      rows.push(row('전압 등급', kvText(item.kv)), row('좌표', `${num(item.lat, 4)}, ${num(item.lon, 4)}`), row('연결 선로', `${item.degree}개`));
      if (item.gens.length) rows.push(row('발전기 (지도 API)', item.gens.map((gen) => `${gen.generator_id}${gen.connected === false ? ' (미접속)' : ''}`).join(', ')));
      if (item.plants.length) rows.push(row('연계 발전소', item.plants.map((plant) => plant.name || plant.record_id).join(', ')));
      const result = pf && pf.buses.get(item.id);
      if (result) {
        rows.push(row('PF |V|', finite(result.v_mag_pu) ? `${fmtPu(result.v_mag_pu)} pu` : 'N/A'), row('PF 위상각', finite(result.v_ang_deg) ? `${num(result.v_ang_deg, 2)}°` : 'N/A'),
          row('제어', result.control), row('전압 한계', finite(result.voltage_min_pu) && finite(result.voltage_max_pu) ? `${fmtPu(result.voltage_min_pu)}–${fmtPu(result.voltage_max_pu)} pu` : null),
          row('전압 위반', result.voltage_violation === true ? '있음' : result.voltage_violation === false ? '없음' : 'N/A'), row('해석 상태', result.analysis_status));
      } else if (pf) rows.push(row('PF', pf.excluded.buses.has(item.id) ? 'PF 해석 제외 모선' : 'PF 결과 없음'));
    } else if (hit.kind === 'line') {
      rows.push(row('구간', `${busLabel(item.from)} — ${busLabel(item.to)}`), row('전압 등급', kvText(item.kv)),
        row('길이', finite(item.raw.length_km) ? `${num(item.raw.length_km, 2)} km` : null),
        row('표시 경로', {gis: 'GIS 경로 (OSM 원본 조각, connector 미표시)', route: 'OSM 경로', 'straight-fallback': '직선 (GIS 경로 미적용)', straight: '직선 (좌표 없음)'}[item.source]));
      if (typeof item.raw.electrical_parameters_source === 'string') rows.push(row('전기 정수', item.raw.electrical_parameters_source));
      const result = pf && pf.lines.get(item.id);
      if (result) {
        rows.push(row('PF 부하율', finite(result.loading_percent) ? fmtPct(result.loading_percent) : 'N/A'), row('정격', finite(result.rating_mva) ? `${num(result.rating_mva, 1)} MVA` : 'N/A'),
          row('P (모선1→2)', finite(result.p0_mw) ? `${num(result.p0_mw, 2)} MW` : null), row('Q (모선1)', finite(result.q0_mvar) ? `${num(result.q0_mvar, 2)} Mvar` : null),
          row('열적 위반', result.thermal_violation === true ? '있음' : result.thermal_violation === false ? '없음' : 'N/A'), row('해석 상태', result.analysis_status));
      } else if (pf) rows.push(row('PF', pf.excluded.lines.has(item.id) ? 'PF 해석 제외 선로' : 'PF 결과 없음'));
      rows.push(row('선로 사고 해석', 'Phase 3 연동 예정 (미연동)'));
    } else if (hit.kind === 'plant') {
      title = `${KIND_LABEL.plant} ${item.name || item.id}`;
      rows.push(row('레코드', item.id), row('연료', item.raw.fueltype), row('설비 용량', finite(item.raw.capacity_mw) ? fmtMW(item.raw.capacity_mw) : null),
        row('연계 모선', item.raw.mapped_bus_id ?? '연계 없음'), row('운영 상태', item.raw.current_operational_status));
    } else if (hit.kind === 'transformer') {
      rows.push(row('구간', `${busLabel(item.from)} — ${busLabel(item.to)}`), row('추정 여부', item.raw.inferred === true ? '추정 변압기' : item.raw.inferred === false ? '원본' : null));
      const result = pf && pf.transformers.get(item.id);
      if (result) rows.push(row('PF 부하율', finite(result.loading_percent) ? fmtPct(result.loading_percent) : 'N/A'), row('정격', finite(result.rating_mva) ? `${num(result.rating_mva, 1)} MVA` : 'N/A'));
    }
    card.replaceChildren(
      h('div', {class: 'kems-cardhead'}, h('b', {}, title), h('button', {type: 'button', class: 'btn', onclick: () => { apiMap.clearSelection(); }}, '닫기')),
      h('div', {class: 'kems-cardsrc'}, `EMS API · ${view.gridId}${pf ? ' · PF 미리보기 (조회 전용)' : ''}`),
      ...rows.filter(Boolean));
    card.hidden = false;
    card.dataset.kind = hit.kind;
    card.dataset.id = item.id;
  }

  /* ---------- rendering of the API view ---------- */
  function renderDesc(view) {
    const entry = view.entry;
    const lines = [h('div', {class: 'kems-descmain'}, h('b', {}, apiName(entry, view.gridId)), typeof (entry && entry.source) === 'string' ? ` · ${entry.source}` : '')];
    const facts = [];
    if (view.map.status === 'ready') {
      const m = view.map.model;
      facts.push(`지도: 모선 ${m.buses.length} · 선로 ${m.lines.length} · 변압기 ${m.transformers.length}`
        + (view.map.data.generators && view.map.data.generators.length ? ` · 발전기 ${view.map.data.generators.length}` : '')
        + (m.plants.length ? ` · 발전소 ${m.plants.length}` : ''));
      if (m.missingEnds) facts.push(`끝점 모선이 없는 선로 ${m.missingEnds}개 미표시`);
    } else if (view.map.status === 'loading') facts.push('지도 불러오는 중');
    else if (view.map.status === 'error') facts.push(`지도 오류: ${view.map.error}`);
    if (DISPLAY_GEOMETRY[view.gridId] && view.map.status === 'ready') facts.push(geometryText(view));
    lines.push(h('div', {class: 'kems-descsub', 'data-gis': view.geometry.status}, facts.join(' · ')));
    descBox.replaceChildren(...lines);
  }

  function geometryText(view) {
    const g = view.geometry;
    const m = view.map.model;
    if (g.status === 'ready') {
      return `GIS 경로 ${m.gis}선로 (OSM 조각 ${g.parts}) · 직선 ${m.straightFallback}선로 · 합성 connector 미표시`
        + (g.invalid ? ` · 검증 실패 경로 ${g.invalid}개 직선 표시` : '');
    }
    return `GIS 표시 자료 ${g.status === 'invalid' ? '검증 실패' : '없음'} → 모든 선로 직선 표시 (지도 데이터는 정상)${g.error ? ` · ${g.error}` : ''}`;
  }

  function kpiTile(key, label, value, note, tone) {
    return h('div', {class: `kpi${tone ? ` kems-${tone}` : ''}`, 'data-kpi': key, title: note}, h('span', {}, label), h('b', {}, value), h('em', {}, note));
  }

  function renderKpis(view) {
    const pf = view.pf;
    const labels = [['load', '총 부하'], ['voltage', '전압 범위 (pu)'], ['loading', '최대 선로 부하율'], ['violations', 'PF 기준 위반']];
    if (pf.status !== 'ready') {
      const note = {
        idle: '확인 중', loading: view.status === 'error' ? '확인 불가' : 'PF 미리보기 불러오는 중', unsupported: '미지원 · EMS API에 PF 미리보기 없음 (지도 전용)',
        unknown: 'PF 지원 여부 응답에 없음', error: `PF 조회 실패 · ${pf.error}`, invalid: `PF 검증 실패 · ${pf.error}`, blocked: pf.error,
        nonconverged: `PF 비수렴 (${pf.meta && pf.meta.pf_status}) · 수치 표시 안 함`,
      }[pf.status] || '확인 불가';
      const status = view.status === 'error' || view.status === 'missing' ? (view.status === 'missing' ? 'EMS API 목록에 없음' : `확인 불가 · ${view.error}`) : note;
      kpis.replaceChildren(...labels.map(([key, label]) => kpiTile(key, label, 'N/A', status, pf.status === 'unsupported' ? 'muted' : pf.status === 'loading' || pf.status === 'idle' ? 'muted' : 'err')));
      return;
    }
    const meta = pf.meta;
    const summary = pf.summary;
    const sv = summary.screening_violations && typeof summary.screening_violations === 'object' ? summary.screening_violations : null;
    const counted = (key, label) => (sv && finite(sv[key]) ? `${label} ${sv[key]}` : null);
    const violations = sv ? [counted('bus_voltage', '전압'), counted('line_thermal', '선로'), counted('transformer_thermal', '변압기')].filter(Boolean) : [];
    const generator = sv ? [counted('generator_p', 'P'), counted('generator_q', 'Q'), counted('generator_s', 'S')].filter(Boolean) : [];
    const anyViolation = sv && Object.values(sv).some((value) => finite(value) && value > 0);
    kpis.replaceChildren(
      kpiTile('load', '총 부하', finite(meta.load_mw) ? fmtMW(meta.load_mw) : 'N/A',
        `발전 합계: 응답에 없음${finite(meta.load_mvar) ? ` · 무효 ${num(meta.load_mvar, Math.abs(meta.load_mvar) >= 100 ? 0 : 2)} Mvar` : ''}`),
      kpiTile('voltage', '전압 범위 (pu)', finite(summary.voltage_min_pu) && finite(summary.voltage_max_pu) ? `${fmtPu(summary.voltage_min_pu)} – ${fmtPu(summary.voltage_max_pu)}` : 'N/A',
        `PF 해석 모선 ${pf.buses.size}개`),
      kpiTile('loading', '최대 선로 부하율', finite(summary.line_max_loading_percent) ? fmtPct(summary.line_max_loading_percent) : 'N/A',
        finite(summary.transformer_max_loading_percent) ? `변압기 최대 ${fmtPct(summary.transformer_max_loading_percent)}` : '변압기 결과 없음',
        finite(summary.line_max_loading_percent) && summary.line_max_loading_percent >= 100 ? 'err' : finite(summary.line_max_loading_percent) && summary.line_max_loading_percent >= 80 ? 'warn' : null),
      kpiTile('violations', 'PF 기준 위반', violations.length ? violations.join(' · ') : 'N/A',
        generator.length ? `발전기 ${generator.join(' · ')}` : (typeof meta.screening_status === 'string' ? meta.screening_status : '스크리닝 기준 응답에 없음'),
        anyViolation ? 'err' : null),
    );
  }

  function renderLegend(view) {
    if (view.map.status !== 'ready') { legend.replaceChildren(); return; }
    const m = view.map.model;
    const items = m.kvs.slice(0, 3).map((kv, i) => h('span', {}, h('i', {class: `kems-lgline k-v${i + 1}`}), kvText(kv)));
    if (m.kvs.length > 3) items.push(h('span', {}, h('i', {class: 'kems-lgline k-v3'}), `${m.kvs.slice(3).map((kv) => num(kv, 0)).join('/')} kV`));
    if (m.buses.some((bus) => bus.feeds)) items.push(h('span', {}, h('i', {class: 'kems-lgdot feed'}), m.plants.length ? '발전소 연계 모선' : '발전기 모선'));
    items.push(h('span', {}, h('i', {class: 'kems-lgdot'}), '모선'));
    if (m.plants.length) items.push(h('span', {}, h('i', {class: 'kems-lgsq plant'}), '발전소'));
    if (m.transformers.length) items.push(h('span', {}, h('i', {class: 'kems-lgsq trafo'}), '변압기'));
    if (m.straightFallback) items.push(h('span', {}, h('i', {class: 'kems-lgline dashed'}), '직선 (GIS 경로 미적용)'));
    if (view.pf.status === 'ready' && view.mode === 'loading') {
      items.push(h('span', {}, h('i', {class: 'kems-lgline ld-ok'}), '부하율 < 80%'), h('span', {}, h('i', {class: 'kems-lgline ld-high'}), '80–100%'),
        h('span', {}, h('i', {class: 'kems-lgline ld-over'}), '≥ 100% 또는 위반'), h('span', {}, h('i', {class: 'kems-lgline ld-none'}), 'PF 결과 없음·해석 제외'),
        h('span', {}, h('i', {class: 'kems-lgdot alert'}), '전압 위반 모선'));
    }
    items.push(h('span', {class: 'kems-lgsrc'}, view.pf.status === 'ready' && view.mode === 'loading' ? '색: PF 미리보기 (조회 전용)' : '선 굵기·밝기: 전압 등급'));
    legend.replaceChildren(...items);
  }

  function renderMapMessage(view) {
    let text = '';
    if (view.status === 'loading' || view.status === 'idle') text = 'EMS API 계통 정보를 확인하는 중…';
    else if (view.status === 'error') text = `EMS API 기능 상태를 확인하지 못했습니다: ${view.error}`;
    else if (view.status === 'missing') text = `최신 EMS API 목록에 ${view.gridId} 계통이 없습니다.`;
    else if (view.map.status === 'loading') text = '실제 계통 지도를 불러오는 중…';
    else if (view.map.status === 'error') text = `지도를 불러오지 못했습니다: ${view.map.error} · 데모 지도로 대체하지 않습니다.`;
    else if (view.map.status === 'unavailable') text = 'EMS API에서 이 계통의 지도를 제공하지 않습니다.';
    mapMessage.textContent = text;
    mapMessage.hidden = !text;
    mapMessage.dataset.tone = /못했|없습니다|제공하지/.test(text) ? 'err' : 'info';
  }

  function capabilityTable(entry) {
    const known = new Set(CAPABILITIES.map(([flag]) => flag));
    const rows = [...CAPABILITIES, ...availableFlags(entry).filter((flag) => !known.has(flag)).map((flag) => [flag, flag, '미연동'])];
    return h('table', {class: 'reg kems-cap'},
      h('tr', {}, h('th', {}, '기능'), h('th', {}, 'EMS API'), h('th', {}, '이 콘솔')),
      rows.map(([flag, label, wired]) => {
        const value = entryFlag(entry, flag);
        const note = CAPABILITY_NOTES[flag] && typeof entry[CAPABILITY_NOTES[flag]] === 'string' ? entry[CAPABILITY_NOTES[flag]] : null;
        return h('tr', {'data-flag': flag},
          h('td', {}, label, h('div', {class: 'd'}, h('code', {}, flag)), note ? h('div', {class: 'd kems-capnote'}, note) : null),
          h('td', {}, value === true ? h('span', {class: 'kems-y'}, '제공') : value === false ? h('span', {class: 'kems-n'}, '미제공') : h('span', {class: 'kems-u'}, '응답에 없음')),
          h('td', {}, value === true ? h('span', {class: wired === '미연동' ? 'kems-w' : 'kems-y'}, wired) : h('span', {class: 'kems-u'}, '—')));
      }));
  }

  function pfSection(view) {
    const pf = view.pf;
    const head = h('h3', {}, 'PF 미리보기');
    if (pf.status === 'unsupported' || pf.status === 'unknown') {
      const meta = view.map.status === 'ready' && view.map.data.metadata && typeof view.map.data.metadata === 'object' ? view.map.data.metadata : null;
      return h('section', {class: 'kems-sec', 'data-sec': 'pf'}, head,
        h('p', {class: 'kems-msg'}, pf.status === 'unsupported' ? '미지원 · 이 계통은 지도 전용입니다 (EMS API pf_preview_available = false). AC PF·N-1·Sensitivity 결과를 표시하지 않습니다.' : 'PF 지원 여부가 응답에 없습니다.'),
        meta ? h('div', {class: 'kems-facts'}, row('모델 종류', meta.model_kind), row('AC PF 준비', meta.ac_pf_readiness), row('운영 데이터', meta.operational === false ? '아님' : meta.operational === true ? '예' : null),
          row('OSM 기준 시각', meta.osm_data_timestamp), row('출처', meta.attribution), row('라이선스', meta.license)) : null);
    }
    if (pf.status !== 'ready' && pf.status !== 'nonconverged') {
      const text = {idle: '확인 중', loading: 'PF 미리보기를 불러오는 중입니다.', error: `PF 조회 실패: ${pf.error}`, invalid: `PF 검증 실패: ${pf.error}`, blocked: pf.error}[pf.status] || '확인 불가';
      return h('section', {class: 'kems-sec', 'data-sec': 'pf'}, head, h('p', {class: `kems-msg${/실패|오류/.test(text) ? ' kems-err' : ''}`}, text,
        /실패|오류/.test(text) ? ' · 데모 결과로 대체하지 않습니다.' : ''));
    }
    const meta = pf.meta;
    const summary = pf.summary;
    const badges = [
      h('span', {class: `kems-pill ${pf.converged ? 'kems-pill-ok' : 'kems-pill-err'}`}, pf.converged ? 'AC PF 수렴' : `비수렴 · ${meta.pf_status}`),
      meta.synthetic_scenario === true ? h('span', {class: 'kems-pill kems-pill-loading'}, '합성 시나리오') : null,
      meta.actual_operating_data === false ? h('span', {class: 'kems-pill kems-pill-loading'}, '실운영 데이터 아님') : null,
      h('span', {class: 'kems-pill kems-pill-loading'}, '조회 전용 · 실시간 아님'),
    ];
    const hvdc = meta.non_ac_equipment && meta.non_ac_equipment.hvdc_lines;
    const residual = summary.power_balance_residual && typeof summary.power_balance_residual === 'object' && finite(summary.power_balance_residual.max_nodal_residual_mva)
      ? `${summary.power_balance_residual.max_nodal_residual_mva.toExponential(2)} MVA` : null;
    const mapModel = view.map.model;
    return h('section', {class: 'kems-sec', 'data-sec': 'pf'}, head, h('div', {class: 'kems-badges'}, badges),
      h('div', {class: 'kems-facts'},
        row('시나리오', meta.scenario_id), row('스냅샷', meta.snapshot), row('원천 모델', meta.source_model), row('스크리닝', meta.screening_status),
        row('신뢰도', summary.model_reliability_status), row('반복 횟수', finite(summary.iteration_count) ? summary.iteration_count : null), row('최대 노드 잔차', residual),
        row('PF 해석 모델', `모선 ${pf.buses.size} · 선로 ${pf.lines.size} · 변압기 ${pf.transformers.size}`),
        row('지도 (지리 표시)', mapModel ? `모선 ${mapModel.buses.length} · 선로 ${mapModel.lines.length} · 변압기 ${mapModel.transformers.length}` : null),
        row('PF 해석 제외', pf.excluded.buses.size || pf.excluded.lines.size ? `모선 ${pf.excluded.buses.size} · 선로 ${pf.excluded.lines.size}` : null),
        row('PF 결과 없는 지도 설비', pf.missing.buses || pf.missing.lines ? `모선 ${pf.missing.buses} · 선로 ${pf.missing.lines}` : null),
        row('비AC 설비', hvdc && finite(hvdc.count) ? `HVDC ${hvdc.count}회선 (${hvdc.analysis_status || 'AC 선로 결과 미포함'})` : null),
        row('전압 한계 출처', typeof meta.voltage_limit_source === 'string' ? meta.voltage_limit_source : null)));
  }

  function topLinesSection(view) {
    const pf = view.pf;
    if (pf.status !== 'ready') return null;
    const ranked = [...pf.lines.values()].filter((line) => finite(line.loading_percent)).sort((a, b) => b.loading_percent - a.loading_percent).slice(0, 8);
    const violations = [...pf.buses.values()].filter((bus) => bus.voltage_violation === true).map((bus) => ['bus', idOf(bus.bus_id), `전압 ${finite(bus.v_mag_pu) ? fmtPu(bus.v_mag_pu) : 'N/A'} pu`])
      .concat([...pf.lines.values()].filter((line) => line.thermal_violation === true).map((line) => ['line', idOf(line.line_id), `부하율 ${fmtPct(line.loading_percent)}`]));
    const model = view.map.model;
    const ends = (line) => {
      const a = model.busById.get(idOf(line.bus1_id)), b = model.busById.get(idOf(line.bus2_id));
      return `${a ? busLabel(a) : idOf(line.bus1_id)} — ${b ? busLabel(b) : idOf(line.bus2_id)}`;
    };
    return [
      h('section', {class: 'kems-sec', 'data-sec': 'top'}, h('h3', {}, '부하율 상위 선로 · PF 미리보기'),
        ranked.map((line) => h('button', {type: 'button', class: 'kems-rank', 'data-line': idOf(line.line_id), onclick: () => apiMap.select('line', idOf(line.line_id), true)},
          h('span', {}, h('b', {}, idOf(line.line_id)), h('em', {}, ends(line))),
          h('i', {class: line.loading_percent >= 100 ? 'kems-err' : line.loading_percent >= 80 ? 'kems-warnt' : ''}, fmtPct(line.loading_percent))))),
      h('section', {class: 'kems-sec', 'data-sec': 'violations'}, h('h3', {}, 'PF 기준 위반 설비'),
        violations.length ? violations.map(([kind, id, text]) => h('button', {type: 'button', class: 'kems-rank', onclick: () => apiMap.select(kind, id, true)},
          h('span', {}, h('b', {}, `${KIND_LABEL[kind]} ${id}`)), h('i', {class: 'kems-err'}, text)))
          : h('p', {class: 'kems-msg'}, '위반 없음 (PF 미리보기 스크리닝 기준)')),
    ];
  }

  function renderSide(view) {
    const entry = view.entry;
    const capability = view.status === 'ready' && entry
      ? [capabilityTable(entry), h('div', {class: 'kems-sub'}, `GET /api/grids 응답 그대로 · 확인 ${clock(state.checkedAt)}`)]
      : h('p', {class: `kems-msg${view.status === 'error' || view.status === 'missing' ? ' kems-err' : ''}`},
        view.status === 'loading' ? 'GET /api/grids 응답을 기다리는 중입니다. 확인 전에는 기능 상태를 표시하지 않습니다.'
          : view.status === 'missing' ? `최신 EMS API 목록에 ${view.gridId} 계통이 없습니다.` : `EMS API 기능 상태를 확인하지 못했습니다: ${view.error}`);
    const sections = [
      h('div', {class: 'kems-sidehead'}, h('span', {class: 'kems-src kems-src-api'}, 'EMS API 계통'), h('b', {}, apiName(entry, view.gridId)), h('code', {}, view.gridId),
        h('button', {type: 'button', class: 'btn', onclick: () => openView(view.gridId)}, '다시 불러오기')),
      view.status === 'ready' ? pfSection(view) : null,
      view.status === 'ready' ? topLinesSection(view) : null,
      h('section', {class: 'kems-sec', 'data-sec': 'capability'}, h('h3', {}, 'EMS API 기능 상태'), capability),
      h('section', {class: 'kems-sec kems-notwired', 'data-sec': 'notwired'}, h('h3', {}, '사고 해석 · AI 채팅'),
        h('p', {class: 'kems-msg'}, 'N-1·Sensitivity 해석(Phase 3)과 AI 채팅(Phase 4)은 아직 이 콘솔에 연동되지 않았습니다. Artifact의 데모 규칙 엔진·사고 주입은 EMS API 계통에 실행하지 않습니다.'),
        h('a', {href: '/'}, '기존 화면에서 해석 열기')),
      entry && typeof entry.note === 'string' ? h('p', {class: 'note kems-note'}, entry.note) : null,
    ];
    side.replaceChildren(...sections.flat().filter(Boolean));  // replaceChildren() does not flatten arrays
  }

  /* ---------- header ---------- */
  const liveButton = document.querySelector('.mtog button[data-m="live"]');
  liveButton.textContent = '실계통 연동 (모의)';
  liveButton.title = '모의 실시간 피드 (KPG193 데모 기반) · 실제 운영계통과 연결되어 있지 않습니다';
  document.querySelector('.mtog button[data-m="pub"]').title = '공개 데모 계통 · 브라우저 DC 계산';
  document.getElementById('snap').parentElement.classList.add('kems-demo-only');

  function sourceBadge(kind, text, title) {
    return h('span', {class: `kems-src kems-src-${kind}`, title}, text);
  }

  const demoHdrUpdate = hdrUpdate;
  hdrUpdate = function (...args) {
    const info = document.getElementById('ctxInfo');
    if (state.source === 'api') {
      const view = state.view;
      const tw = document.getElementById('twMode');
      tw.textContent = 'EMS API';
      tw.className = 'twm tw-api';
      tw.title = 'EMS API 계통 · 지도와 PF 미리보기(조회 전용) 연동 · 해석·채팅 미연동';
      const mapText = !view ? '' : {ready: '지도 연동', loading: '지도 불러오는 중', error: '지도 오류', unavailable: '지도 미제공'}[view.map.status] || '확인 중';
      const pfText = !view ? '' : {ready: 'PF 미리보기 · 조회 전용', nonconverged: 'PF 비수렴', unsupported: 'PF 미지원', loading: 'PF 불러오는 중',
        error: 'PF 조회 실패', invalid: 'PF 검증 실패', blocked: 'PF 표시 안 함'}[view.pf.status] || 'PF 확인 중';
      info.replaceChildren(
        sourceBadge('api', 'EMS API 계통', '실제 EMS API가 제공하는 계통'),
        h('b', {}, apiName(view && view.entry, state.gridId)),
        h('span', {class: 'ctxs'}, state.gridId),
        h('span', {class: 'ctxm'}, mapText),
        h('span', {class: 'ctxm'}, '해석·채팅 미연동'),
        h('span', {class: 'ctxm'}, pfText),  // fitCtx() hides the other items first when space runs out
      );
      fitCtx();
      return;
    }
    demoHdrUpdate.apply(this, args);
    if (MODE === 'live') {
      const label = info.querySelector(':scope > b');
      if (label && label.textContent === '실계통 연동') label.textContent = '실계통 연동 (모의)';
      info.prepend(sourceBadge('sim', '모의 피드 · 실계통 미연결', '모의 실시간 피드 (KPG193 데모 기반) · 실제 운영계통 데이터가 아닙니다'));
    } else {
      info.prepend(sourceBadge('demo', '데모 계통', 'Artifact 데모 계통 · 브라우저 DC 계산 · 예시 데이터'));
    }
    fitCtx();
  };

  /* ---------- demo screen gates ---------- */
  const gateButtons = [];
  for (const view of document.querySelectorAll('main > .view')) {
    if (view.id === 'v-ops') continue;
    const button = h('button', {type: 'button', class: 'btn pri', onclick: backToDemo});
    gateButtons.push(button);
    const gate = h('div', {class: 'panel kems-gate', role: 'note'},
      h('b', {}, 'Artifact 데모 화면'),
      h('p', {}, '이 화면은 브라우저 DC 계산과 예시 데이터로 동작하는 데모입니다. EMS API 계통이 선택된 동안에는 데모 값이 실제 계통 결과로 오인되지 않도록 표시하지 않습니다.'),
      button);
    const head = view.querySelector(':scope > .vhead');
    if (head) head.after(gate); else view.prepend(gate);
  }

  function render() {
    const api = state.source === 'api';
    document.body.dataset.kemsSrc = state.source;
    for (const button of document.querySelectorAll('.mtog button')) button.disabled = api;
    document.getElementById('apBtn').disabled = api;
    for (const button of gateButtons) button.textContent = `데모 계통으로 전환 · ${DEMO_LABELS[state.lastDemo] || state.lastDemo}`;
    renderOptions();
    const view = api ? state.view : null;
    if (view) {
      renderDesc(view);
      renderMapMessage(view);
      renderKpis(view);
      renderLegend(view);
      renderSide(view);
      const pfReady = view.pf.status === 'ready';
      for (const button of modeToggle.querySelectorAll('button')) {
        button.setAttribute('aria-pressed', String(button.dataset.mode === view.mode));
        button.disabled = button.dataset.mode === 'loading' && !pfReady;
      }
      modeToggle.title = pfReady ? '선로 색: 전압 등급 또는 PF 미리보기 부하율' : 'PF 미리보기가 없거나 확인되지 않아 전압 등급만 표시합니다';
      mapHost.dataset.status = view.map.status;
    } else {
      for (const node of [descBox, kpis, legend, side]) node.replaceChildren();
      mapMessage.hidden = true;
      mapHost.dataset.status = '';
    }
    hdrUpdate();
  }

  // Read-only hook for browser regression probes.
  const ready = refreshCatalog();
  window.kemsConsole = Object.freeze({
    ready,
    state: () => {
      const view = state.view;
      const model = view && view.map.status === 'ready' ? view.map.model : null;
      return {
        source: state.source,
        gridId: state.gridId,
        lastDemo: state.lastDemo,
        catalogError: state.catalogError,
        grids: state.catalog ? state.catalog.grids.map((grid) => grid.id) : null,
        view: view ? {
          gridId: view.gridId, status: view.status, error: view.error || null, mode: view.mode, selection: view.selection,
          map: {status: view.map.status, error: view.map.error || null, counts: model ? {buses: model.buses.length, lines: model.lines.length,
            transformers: model.transformers.length, plants: model.plants.length, gis: model.gis, straightFallback: model.straightFallback} : null},
          geometry: {status: view.geometry.status, parts: view.geometry.parts ?? null, invalid: view.geometry.invalid ?? null, error: view.geometry.error || null},
          pf: {status: view.pf.status, error: view.pf.error || null, counts: view.pf.buses ? {buses: view.pf.buses.size, lines: view.pf.lines.size,
            transformers: view.pf.transformers.size} : null},
        } : null,
      };
    },
    map: Object.freeze({counts: () => apiMap.counts(), viewBox: () => apiMap.viewBox(), screenPoint: (kind, id) => apiMap.screenPoint(kind, id)}),
  });
  render();
})();
