/* K-EMS console bridge.
 *
 * app.js is the Artifact demo, kept byte-identical. This classic script runs after it in the same
 * global scope and adds the EMS API grids from GET /api/grids beside the demo grids, wrapping
 * app.js functions the same way app.js wraps its own (const _f=f; f=function(){...}).
 *
 * While an EMS API grid is selected the Artifact DC engine and the demo screens stay idle and
 * hidden: nothing computed in the browser is ever shown as an EMS result, and an API failure never
 * falls back to demo numbers. Map, PF, analysis and chat of API grids are not wired yet and are
 * labelled "미연동". Everything lives in this IIFE so no app.js global lexical name can collide.
 */
(() => {
  'use strict';

  const API_PREFIX = 'api:';
  const CATALOG_URL = '/api/grids';
  const CATALOG_TIMEOUT_MS = 10000;
  // Short display names only; grid IDs and capabilities always come from GET /api/grids.
  const API_ALIASES = {'kr-kpg193': 'KPG-193', 'mn-osm-35k': '몽골 OSM', 'kr-osm-transmission': '한국 OSM'};
  const DEMO_LABELS = {kpg193: 'KPG193 (DC 데모)', ieee39: 'IEEE39 (데모)', ieee14: 'IEEE14 (데모)', six: '가상 6모선 (데모)'};
  // A flag missing from the response is shown as missing, never assumed.
  const CAPABILITIES = [
    ['map_available', '지도'],
    ['pf_preview_available', 'PF 미리보기'],
    ['security_analysis_available', '선로 N-1 Security Analysis'],
    ['sensitivity_analysis_available', 'Sensitivity Analysis'],
    ['generator_contingency_available', '발전기 탈락 해석'],
    ['chat_available', 'AI 채팅'],
    ['analysis_available', 'KPG 해석 묶음 (호환 플래그)'],
  ];
  const CAPABILITY_NOTES = {security_analysis_available: 'security_analysis_note', sensitivity_analysis_available: 'sensitivity_analysis_note'};

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
    panel: null,        // {status: 'loading'|'ready'|'error'|'missing', entry, error}
  };

  /* ---------- small DOM helpers (text only, never innerHTML with API data) ---------- */
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

  /* ---------- EMS API catalog ---------- */
  class CatalogError extends Error {}

  function validateCatalog(payload) {
    if (!payload || !Array.isArray(payload.grids)) throw new CatalogError('응답 형식 오류 (grids 없음)');
    const grids = [];
    const seen = new Set();
    for (const entry of payload.grids) {
      if (!entry || typeof entry !== 'object' || typeof entry.id !== 'string' || !entry.id || seen.has(entry.id)) continue;
      seen.add(entry.id);
      grids.push(entry);
    }
    if (!grids.length) throw new CatalogError('응답에 계통이 없습니다');
    return {grids, skipped: payload.grids.length - grids.length};
  }

  async function fetchCatalog() {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), CATALOG_TIMEOUT_MS);
    try {
      const response = await fetch(CATALOG_URL, {signal: controller.signal, cache: 'no-store', headers: {Accept: 'application/json'}});
      const text = await response.text();
      let payload = null;
      try { payload = JSON.parse(text); } catch (error) { payload = null; }
      if (!response.ok) {
        const detail = payload && typeof payload.detail === 'string' ? ` · ${payload.detail}` : '';
        throw new CatalogError(`HTTP ${response.status}${detail}`);
      }
      return validateCatalog(payload);
    } catch (error) {
      if (error instanceof CatalogError) throw error;
      throw new CatalogError(controller.signal.aborted ? `응답 시간 초과 (${CATALOG_TIMEOUT_MS / 1000}초)` : 'EMS API 서버에 연결할 수 없습니다');
    } finally {
      clearTimeout(timer);
    }
  }

  let catalogSeq = 0;
  async function refreshCatalog() {
    const seq = ++catalogSeq;
    let catalog = null;
    let error = null;
    try { catalog = await fetchCatalog(); } catch (failure) { error = failure.message; }
    if (seq !== catalogSeq) return;  // a newer request owns the result
    if (catalog) {
      state.catalog = catalog;
      state.catalogError = null;
      state.checkedAt = new Date();
    } else {
      state.catalogError = error;
    }
    if (state.source === 'api') {
      const entry = catalog ? catalog.grids.find((grid) => grid.id === state.gridId) : null;
      state.panel = catalog
        ? {status: entry ? 'ready' : 'missing', entry}
        : {status: 'error', entry: state.panel && state.panel.entry, error};
    }
    render();
  }

  const entryFlag = (entry, flag) => (entry && typeof entry[flag] === 'boolean' ? entry[flag] : null);
  const availableFlags = (entry) => Object.keys(entry).filter((key) => key.endsWith('_available'));
  const mapOnly = (entry) => entry.map_available === true && availableFlags(entry).every((key) => key === 'map_available' || entry[key] !== true);
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

  const retry = h('button', {type: 'button', class: 'btn kems-retry', hidden: true, onclick: () => refreshCatalog()}, 'EMS API 다시 연결');
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
    state.panel = {status: 'loading', entry: cachedEntry(gridId)};
    render();
    refreshCatalog();
  }

  function leaveApi() {
    state.source = 'demo';
    state.gridId = null;
    state.panel = null;
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
      const tw = document.getElementById('twMode');
      tw.textContent = 'EMS API';
      tw.className = 'twm tw-api';
      tw.title = 'EMS API 계통 · 이 콘솔에서는 지도·수치·해석이 아직 연동되지 않았습니다';
      const entry = state.panel && state.panel.entry;
      info.replaceChildren(
        sourceBadge('api', 'EMS API 계통', '실제 EMS API가 제공하는 계통'),
        h('b', {}, apiName(entry, state.gridId)),
        h('span', {class: 'ctxs'}, state.gridId),
        h('span', {class: 'ctxm'}, '지도·수치·해석 미연동'),
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

  /* ---------- demo screen gates and the API panel ---------- */
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

  const apiPanel = h('div', {class: 'kems-api', id: 'kemsApi', 'aria-live': 'polite'});
  const opsHead = document.querySelector('#v-ops > .vhead');
  if (opsHead) opsHead.after(apiPanel); else document.getElementById('v-ops').prepend(apiPanel);

  function capabilityValue(entry, flag) {
    const value = entryFlag(entry, flag);
    if (value === true) return h('span', {class: 'kems-y'}, '제공');
    if (value === false) return h('span', {class: 'kems-n'}, '미제공');
    return h('span', {class: 'kems-u'}, '응답에 없음');
  }

  function capabilityTable(entry) {
    const known = new Set(CAPABILITIES.map(([flag]) => flag));
    const rows = [...CAPABILITIES, ...availableFlags(entry).filter((flag) => !known.has(flag)).map((flag) => [flag, flag])];
    return h('div', {class: 'tw'}, h('table', {class: 'reg kems-cap'},
      h('tr', {}, h('th', {}, '기능'), h('th', {}, 'EMS API'), h('th', {}, '이 콘솔')),
      rows.map(([flag, label]) => {
        const note = CAPABILITY_NOTES[flag] && typeof entry[CAPABILITY_NOTES[flag]] === 'string' ? entry[CAPABILITY_NOTES[flag]] : null;
        return h('tr', {'data-flag': flag},
          h('td', {}, label, h('div', {class: 'd'}, h('code', {}, flag)), note ? h('div', {class: 'd kems-capnote'}, note) : null),
          h('td', {}, capabilityValue(entry, flag)),
          h('td', {}, entryFlag(entry, flag) === true ? h('span', {class: 'kems-w'}, '미연동') : h('span', {class: 'kems-u'}, '—')));
      })));
  }

  function pendingKpis(entry, status) {
    const unknown = status === 'loading' ? '확인 중' : '확인 불가';
    const pf = status === 'ready' ? entryFlag(entry, 'pf_preview_available') : undefined;
    const pfNote = status !== 'ready' ? unknown : pf === true ? '미연동 · EMS PF 미리보기 제공' : pf === false ? 'EMS API 미제공' : '응답에 없음';
    const tile = (label, note) => h('div', {class: 'kpi'}, h('span', {}, label), h('b', {}, '—'), h('em', {}, note));
    return h('div', {class: 'kpis kems-kpis'},
      tile('최대 선로 부하율', pfNote),
      tile('전압 범위 (pu)', pfNote),
      tile('PF 기준 위반', pfNote),
      tile('N-1 위반 사고', status === 'ready' ? '전 선로 N-1 스크리닝 API 없음' : unknown));
  }

  function placeholder(title, text) {
    return h('div', {class: 'kems-ph'}, h('b', {}, title), h('p', {}, text));
  }

  function renderApiPanel() {
    if (state.source !== 'api') {
      apiPanel.replaceChildren();
      return;
    }
    const {status, entry, error} = state.panel;
    const ready = status === 'ready';
    const name = apiName(entry, state.gridId);
    const pill = {
      loading: ['loading', 'EMS API 기능 상태 확인 중…'],
      ready: ['ok', `EMS API 응답 확인 ${clock(state.checkedAt)}`],
      error: ['err', 'EMS API 확인 실패'],
      missing: ['err', 'EMS API 목록에 없음'],
    }[status];
    const refresh = h('button', {type: 'button', class: 'btn', onclick: () => {
      state.panel = {status: 'loading', entry: state.panel.entry};
      render();
      refreshCatalog();
    }}, '다시 확인');

    let mapText = '';
    let chatText = '';
    if (ready) {
      const map = entryFlag(entry, 'map_available');
      mapText = map === true
        ? 'EMS API가 지도 데이터를 제공하지만 이 콘솔의 지도 표시는 아직 연동되지 않았습니다. 기존 화면(/)에서 확인할 수 있습니다.'
        : `EMS API에서 이 계통의 지도를 제공하지 않습니다${typeof entry.status === 'string' ? ` (상태: ${entry.status})` : ''}.`;
      const chat = entryFlag(entry, 'chat_available');
      chatText = chat === true
        ? `EMS API가 AI 채팅을 제공하지만${typeof entry.chat_mode === 'string' ? ` (${entry.chat_mode})` : ''} 이 콘솔에서는 아직 연동되지 않았습니다. Artifact 규칙 엔진은 EMS API 계통에 실행하지 않습니다.`
        : 'EMS API에서 이 계통의 AI 채팅을 지원하지 않습니다.';
    } else {
      mapText = status === 'loading' ? 'EMS API 기능 상태를 확인하는 중입니다.' : '기능 상태를 확인하지 못해 표시하지 않습니다.';
      chatText = mapText;
    }

    const main = h('div', {class: 'panel kems-main'},
      h('div', {class: 'ph'},
        h('div', {}, h('h2', {}, name), h('div', {class: 'sub'}, 'EMS API 계통 · grid_id ', h('code', {}, state.gridId))),
        h('span', {class: `kems-pill kems-pill-${pill[0]}`}, pill[1])),
      h('div', {class: 'kems-honest'},
        h('b', {}, '이 콘솔에서는 아직 지도·KPI·해석 결과를 연동하지 않았습니다.'),
        ' 브라우저 DC 엔진은 실행하지 않으며, 데모 계통의 수치를 대신 표시하지 않습니다. ',
        h('a', {href: '/'}, '기존 화면 열기')),
      pendingKpis(entry, status),
      h('div', {class: 'kems-phs'}, placeholder('지도 · 미연동', mapText), placeholder('의사결정지원 채팅 · 미연동', chatText)),
      ready && typeof entry.note === 'string' ? h('p', {class: 'note kems-note'}, entry.note) : null);

    let body;
    if (ready) {
      const facts = [['출처', entry.source], ['형식', entry.format], ['지도 종류', entry.map_kind], ['상태', entry.status], ['채팅 모드', entry.chat_mode]]
        .filter(([, value]) => typeof value === 'string' && value);
      body = [capabilityTable(entry), h('dl', {class: 'kems-facts'}, facts.map(([key, value]) => [h('dt', {}, key), h('dd', {}, value)]))];
    } else if (status === 'loading') {
      body = [h('p', {class: 'kems-msg'}, 'GET /api/grids 응답을 기다리는 중입니다. 확인 전에는 기능 상태를 표시하지 않습니다.')];
    } else if (status === 'missing') {
      body = [h('p', {class: 'kems-msg kems-err'}, `최신 EMS API 목록에 ${state.gridId} 계통이 없습니다.`)];
    } else {
      body = [h('p', {class: 'kems-msg kems-err'}, `EMS API 기능 상태를 확인하지 못했습니다: ${error}`),
        h('p', {class: 'kems-msg'}, '실패 시 데모 결과로 대체하지 않습니다. 서버 상태를 확인한 뒤 다시 확인하세요.')];
    }
    const caps = h('div', {class: 'panel kems-caps'},
      h('div', {class: 'ph'}, h('div', {}, h('h2', {}, 'EMS API 기능 상태'), h('div', {class: 'sub'}, 'GET /api/grids 응답 그대로 · 콘솔 연동 여부 별도 표시')), refresh),
      body);
    apiPanel.replaceChildren(main, caps);
  }

  function render() {
    const api = state.source === 'api';
    document.body.dataset.kemsSrc = state.source;
    for (const button of document.querySelectorAll('.mtog button')) {
      button.disabled = api;
    }
    document.getElementById('apBtn').disabled = api;
    for (const button of gateButtons) button.textContent = `데모 계통으로 전환 · ${DEMO_LABELS[state.lastDemo] || state.lastDemo}`;
    renderOptions();
    renderApiPanel();
    hdrUpdate();
  }

  // Read-only hook for browser regression probes.
  const ready = refreshCatalog();
  window.kemsConsole = Object.freeze({
    ready,
    state: () => ({
      source: state.source,
      gridId: state.gridId,
      lastDemo: state.lastDemo,
      panel: state.panel ? {status: state.panel.status, error: state.panel.error || null} : null,
      catalogError: state.catalogError,
      grids: state.catalog ? state.catalog.grids.map((grid) => grid.id) : null,
    }),
  });
  render();
})();
