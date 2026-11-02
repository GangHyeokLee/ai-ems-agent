/* Browser probe for the Artifact console (/console/) and, in parity mode, the original Artifact.
 * Injected by tests/test_console_browser.py after the page scripts; the report is written to a
 * hidden pre element (id __probe_result). Modes (?probe=):
 *   parity    demo fingerprint (same steps on the original Artifact and on /console/)
 *   api       EMS API grids: real maps, PF preview KPIs/colours, selection, zoom, demo isolation
 *   stale     held map/PF/catalog responses released after a grid switch (test proxy gates)
 *   fail500 | drop | badjson   initial GET /api/grids failure (forced by the test proxy)
 *   pf500 | map500 | mapdrop | geo404 | geobad | pfgrid | pfnonconv   per-endpoint failures (?scenario=)
 *   timeout   GET /api/grids that never answers (fetch stand-in honouring the abort signal)
 *   fonts     Google Fonts unreachable (Chrome resolver rule)
 *   perf      real-time map rendering, zoom/pan and grid-switch timings (run without virtual time)
 *   shot      end state for a screenshot (&shot=<state>)
 */
(async () => {
  const params = new URLSearchParams(location.search);
  const mode = params.get('probe') || 'parity';
  const results = [];
  const check = (name, pass, detail) => results.push({name, pass: !!pass, detail: detail === undefined ? null : detail});
  const byId = (id) => document.getElementById(id);
  const text = (id) => (byId(id) ? byId(id).textContent : null);
  const visible = (el) => !!el && el.getClientRects().length > 0;
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const frame = () => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  const tick = () => fetch('/__probe__/tick', {cache: 'no-store'});
  const norm = (value) => String(value == null ? '' : value).replace(/\s+/g, ' ')
    .replace(/\d+(\.\d+)?s\b/g, '#s').replace(/\d{1,2}:\d{2}(:\d{2})?/g, 'HH:MM').replace(/\d+\s*(초|분) 전/g, '# 전').trim();
  const kpis = () => ({load: text('kLoad'), gen: text('kGen'), max: text('kMax'), n1: text('kN1')});
  const DEMO_KPG = {load: '55,606 MW', gen: '55,606 MW', max: '98%', n1: '20건'};
  const finish = (extra = {}) => {
    const pre = document.createElement('pre');
    pre.id = '__probe_result';
    pre.hidden = true;
    pre.textContent = JSON.stringify({mode, results, errors: window.__probeErrors || [], ...extra});
    document.body.appendChild(pre);
  };
  async function until(predicate, label, limit = 1500) {
    for (let i = 0; i < limit; i++) {
      if (predicate()) return true;
      await tick();
    }
    check(`wait: ${label}`, false);
    return false;
  }
  const settle = async () => { for (let i = 0; i < 240 && (i < 4 || busy); i++) await wait(500); };
  const choose = (value) => {
    const select = byId('model');
    select.value = value;
    select.dispatchEvent(new Event('change'));
  };
  const role = (name) => {
    const select = byId('roleSel');
    select.value = name;
    select.dispatchEvent(new Event('change'));
  };
  const nav = (view) => document.querySelector(`nav button[data-v="${view}"]`).click();
  const VIEWS = ['ops', 'db', 'kb', 'rp', 'gw', 'dl', 'sn', 'ml', 'ev'];
  const QUERIES = ['현재 계통 상태 알려줘', '양평–태백 선로 휴전하고 서울 부하 10% 늘면?', 'N-1 점검해줘', '그 상태에서 예방 조치 추천해줘',
    '과부하 조치 규정 찾아줘', '보령화력 1호기 정지되면?', '오늘 보고서 써줘', '10분 전 상태 보여줘'];

  /* ---------- parity: identical demo steps on the original Artifact and /console/ ---------- */
  const diagnostics = {};  // reported, not compared
  async function parity() {
    const fp = {};
    await wait(1500);
    fp.initial = {...kpis(), alarms: document.querySelectorAll('#alarms li').length, sld: document.querySelectorAll('#sld *').length,
      injOptions: byId('inj').options.length, chips: [...document.querySelectorAll('#chips .chip')].map((b) => b.textContent),
      desc: text('modelDesc'), placeholder: byId('q').placeholder, msgs: norm(byId('msgs').innerText), title: document.title,
      nav: [...document.querySelectorAll('nav button[data-v]')].map((b) => b.textContent), modelValue: byId('model').value,
      demoModels: [...byId('model').options].map((o) => o.value).filter((v) => !v.startsWith('api:') && v)};

    fp.chat = [];
    for (const q of QUERIES) {
      const before = byId('msgs').children.length;
      byId('q').value = q;
      byId('form').requestSubmit();
      await settle();
      const answers = byId('msgs').querySelectorAll('.a');
      fp.chat.push({q, added: byId('msgs').children.length - before, answer: norm(answers.length ? answers[answers.length - 1].innerText : '')});
    }
    const box = byId('msgs');
    diagnostics.chatScrolledToEnd = Math.abs(box.scrollTop + box.clientHeight - box.scrollHeight) <= 2;
    fp.callLog = text('logCnt');
    fp.afterChat = kpis();

    // Start from 맞춤: app.js animateTo() has no lower clamp on its easing time, so under headless virtual
    // time (rAF stamps behind performance.now) the animated viewBox is not comparable run to run.
    const viewBox = () => byId('sld').getAttribute('viewBox');
    if (animId) cancelAnimationFrame(animId);
    byId('zfit').click(); await wait(300);
    fp.zoom = {start: viewBox()};
    byId('zin').click(); await wait(300); fp.zoom.in = viewBox();
    byId('zout').click(); await wait(300); fp.zoom.out = viewBox();
    byId('zfit').click(); await wait(300); fp.zoom.fit = viewBox();
    diagnostics.mapLabelsAfterFit = [...byId('sld').querySelectorAll('text')].filter(visible).map((t) => t.textContent);

    byId('scnBtn').click();
    await wait(600);
    fp.drawer = {open: visible(byId('drawer')), title: text('drTitle'), items: byId('drBody').querySelectorAll('button').length};
    byId('drClose').click();
    await wait(600);
    fp.drawer.closedAfter = byId('drawer').hidden;

    role('연구자');
    await wait(300);
    byId('injBtn').click();
    await wait(1500);
    fp.inject = {...kpis(), alarms: document.querySelectorAll('#alarms li').length, line: byId('inj').value};
    byId('resetBtn').click();
    await wait(1500);
    fp.reset = kpis();
    role('관제사');
    await wait(300);

    fp.models = {};
    for (const id of ['ieee39', 'ieee14', 'six', 'kpg193']) {
      choose(id);
      await wait(2500);
      fp.models[id] = {...kpis(), desc: text('modelDesc'), sld: document.querySelectorAll('#sld *').length,
        alarms: document.querySelectorAll('#alarms li').length, injOptions: byId('inj').options.length};
    }

    byId('tlOpen').click();
    await wait(4000);
    fp.timeline = {time: text('tlTime'), stat: norm(byId('tlStat').innerText)};
    byId('tlPlay').click();
    await wait(6000);
    fp.timeline.afterPlay = text('tlTime');
    fp.timeline.kpis = kpis();
    byId('tlClose').click();
    await wait(800);

    // Screen text without SVG: map labels, lake packets and the DAG follow rAF animation timing, which
    // differs run to run under headless virtual time (also between two runs of the original).
    fp.views = {};
    for (const view of VIEWS) {
      nav(view);
      await wait(3000);
      const section = byId(`v-${view}`);
      const hideSvg = document.createElement('style');
      hideSvg.textContent = 'svg{display:none!important}';
      document.head.append(hideSvg);
      const body = norm(section.innerText);
      hideSvg.remove();
      // The console's own (hidden in demo) API map SVG is not part of the Artifact's screen.
      fp.views[view] = {on: section.classList.contains('on'), length: body.length, text: body, svg: section.querySelectorAll('svg:not(.kems-svg)').length};
      if (view === 'ops') diagnostics.apiMapHiddenInDemo = !document.querySelector('.kems-svg') || !visible(document.querySelector('.kems-svg'));
    }

    nav('ev');
    await wait(500);
    fp.bench = {};
    for (const id of ['runBench', 'runQB', 'runReg', 'runOpqB', 'runScnB']) {
      byId(id).click();
      for (let i = 0; i < 600 && (i < 6 || byId(id).disabled || busy); i++) await wait(500);
    }
    for (const id of ['bCases', 'bMatch', 'bViol', 'bFix', 'qbPass', 'qbAct', 'qbEv', 'qbFix', 'rgP', 'rgPct', 'rgN', 'opqP', 'scnP']) fp.bench[id] = text(id);
    for (const id of ['bench', 'qb', 'regT', 'opqT', 'scnT', 'qbRuns']) fp.bench[`${id}Rows`] = byId(id).querySelectorAll('tr').length;

    nav('sn');
    await wait(500);
    byId('lkRun').click();
    await wait(8000);
    byId('lkRun').click();
    fp.lake = norm(byId('lkStat').innerText);

    nav('ml');
    await wait(500);
    byId('mlRunBtn').click();
    await wait(20000);
    fp.ml = norm(byId('mlLog').innerText).slice(-300);

    nav('kb');
    await wait(500);
    byId('kbQ').value = '선로 과부하 조치';
    byId('kbGo').click();
    await wait(1500);
    fp.kb = norm(byId('kbRes').innerText).slice(0, 300);

    byId('menuAll').checked = false;
    byId('menuAll').dispatchEvent(new Event('change'));
    await wait(300);
    fp.roles = {};
    for (const name of ['관제사', '책임자', '연구자']) {
      role(name);
      await wait(500);
      fp.roles[name] = [...document.querySelectorAll('nav button[data-v]')].filter((b) => !b.hidden).map((b) => b.dataset.v);
    }
    role('관제사');
    byId('menuAll').checked = true;
    byId('menuAll').dispatchEvent(new Event('change'));
    nav('ops');
    await wait(500);

    document.querySelector('.mtog button[data-m="live"]').click();
    await wait(5000);
    fp.live = {mode: MODE, bodyMode: document.body.dataset.mode, model: byId('model').value, tw: text('twMode'), desc: text('modelDesc'),
      sys: norm(byId('msgs').innerText).slice(0, 200), loadIsNumber: /MW$/.test(text('kLoad'))};
    document.querySelector('.mtog button[data-m="pub"]').click();
    await wait(2000);
    fp.backToPub = {mode: MODE, model: byId('model').value, ...kpis()};
    fp.storageKeys = Object.keys(localStorage).sort();
    return fp;
  }

  /* ---------- EMS API helpers (console only) ---------- */
  const ENGINE = ['dcpf', 'n1fast', 'n1full', 'redispatch', 'shed', 'n1eval', 'preventive', 'xmat', 'balance', 'solve', 'bmat'];
  const engine = {calls: 0};
  function countEngineCalls() {
    for (const name of ENGINE) {
      const original = window[name];
      if (typeof original !== 'function') continue;
      window[name] = function (...args) { engine.calls += 1; return original.apply(this, args); };
    }
  }
  const state = () => window.kemsConsole.state();
  const view = () => state().view;
  const TERMINAL_MAP = ['ready', 'error', 'unavailable'];
  const TERMINAL_PF = ['ready', 'nonconverged', 'invalid', 'error', 'blocked', 'unsupported', 'unknown'];
  const settled = (id) => { const v = view(); return !!v && v.gridId === id && (v.status === 'error' || v.status === 'missing'
    || (TERMINAL_MAP.includes(v.map.status) && TERMINAL_PF.includes(v.pf.status))); };
  async function chooseApi(id) {
    choose(`api:${id}`);
    return until(() => settled(id), `view ${id}`);
  }
  const apiOptions = () => [...byId('model').querySelectorAll('optgroup')[0].children].map((o) => ({value: o.value, label: o.textContent, disabled: o.disabled}));
  const getJson = async (path) => (await fetch(`${path}?__probe=1`, {cache: 'no-store'})).json();
  const hits = async () => (await (await fetch('/__probe__/hits', {cache: 'no-store'})).json()).hits.filter((h) => !h.includes('__probe='));
  const svg = () => document.querySelector('#kemsMapHost .kems-svg');
  const tile = (key) => document.querySelector(`#kemsKpis .kpi[data-kpi="${key}"]`);
  const tileValue = (key) => (tile(key) ? tile(key).querySelector('b').textContent : null);
  const tileNote = (key) => (tile(key) ? tile(key).querySelector('em').textContent : null);
  const numbers = (value) => (String(value).match(/-?\d[\d,]*(\.\d+)?/g) || []).map((n) => Number(n.replace(/,/g, '')));
  const close = (shown, expected, digits) => Math.abs(shown - expected) <= 0.5 * 10 ** -digits + 1e-9;
  const DEMO_FIGURES = ['55,606', '6,254', '20건'];
  const demoHidden = () => !visible(byId('sld')) && !visible(byId('kLoad')) && !visible(byId('alarms')) && !visible(byId('msgs'))
    && !visible(byId('injBtn')) && !visible(byId('resetBtn')) && !visible(byId('tlbar')) && !visible(byId('legend'));

  function pointer(type, x, y) {
    svg().dispatchEvent(new PointerEvent(type, {clientX: x, clientY: y, button: 0, buttons: type === 'pointerup' ? 0 : 1, pointerId: 7, bubbles: true}));
  }
  function clickFeature(kind, id, zoomSteps = 0) {
    // Short lines sit within the click tolerance of their own buses at full extent: magnify first.
    for (let i = 0; i < zoomSteps; i++) {
      const q = window.kemsConsole.map.screenPoint(kind, id);
      svg().dispatchEvent(new WheelEvent('wheel', {clientX: q.x, clientY: q.y, deltaY: -500, bubbles: true, cancelable: true}));
    }
    const p = window.kemsConsole.map.screenPoint(kind, id);
    if (!p) return false;
    pointer('pointerdown', p.x, p.y);
    pointer('pointerup', p.x, p.y);
    return true;
  }
  // Click targets that no other feature shares (parallel KPG circuits have identical straight lines,
  // co-located buses share a point); `prefer` is tried first.
  function clickTargets(map, pf, prefer) {
    const pairs = new Map();
    for (const line of map.lines) { const key = [line.bus1_id, line.bus2_id].map(String).sort().join('|'); pairs.set(key, (pairs.get(key) || 0) + 1); }
    const loading = new Map(pf.lines.map((line) => [String(line.line_id), line.loading_percent]));
    const lines = map.lines.filter((line) => loading.has(String(line.line_id)) && pairs.get([line.bus1_id, line.bus2_id].map(String).sort().join('|')) === 1)
      .sort((a, b) => (String(b.line_id) === String(prefer)) - (String(a.line_id) === String(prefer)) || loading.get(String(b.line_id)) - loading.get(String(a.line_id)));
    const spot = (item) => `${item.latitude.toFixed(3)},${item.longitude.toFixed(3)}`;
    const spots = new Map();
    for (const item of [...map.buses, ...(map.powerplants || [])]) if (Number.isFinite(item.latitude)) spots.set(spot(item), (spots.get(spot(item)) || 0) + 1);
    const pfBuses = new Set(pf.buses.map((bus) => String(bus.bus_id)));
    const bus = map.buses.find((b) => pfBuses.has(String(b.bus_id)) && spots.get(spot(b)) === 1);
    return {line: String(lines[0].line_id), bus: String(bus.bus_id), name: bus.name_korean && bus.name_korean !== String(bus.bus_id) ? bus.name_korean : String(bus.bus_id)};
  }
  const cardText = () => (visible(byId('kemsCard')) ? byId('kemsCard').innerText : '');
  const cardValue = (key) => {
    const row = [...byId('kemsCard').querySelectorAll('.kems-kv')].find((r) => r.querySelector('span').textContent === key);
    return row ? row.querySelector('b').textContent : null;
  };

  async function mapMatchesApi(id, label, api) {
    const counts = window.kemsConsole.map.counts();
    check(`${label}: map drawn from /map (buses, lines, transformers)`, counts && counts.buses === api.buses.length && counts.lines === api.lines.length
      && counts.transformers === (api.transformers || []).length, {counts, api: [api.buses.length, api.lines.length, (api.transformers || []).length]});
    check(`${label}: plants from /map`, counts && counts.plants === (api.powerplants || []).filter((p) => Number.isFinite(p.latitude)).length, counts);
    check(`${label}: few DOM nodes for the whole grid`, counts && counts.nodes < 80, counts && counts.nodes);
    check(`${label}: real map shown in the Artifact map area, demo parts hidden`, visible(svg()) && svg().closest('.mapwrap') && demoHidden());
    check(`${label}: header and desc name the API grid`, text('twMode') === 'EMS API' && byId('ctxInfo').textContent.includes(id)
      && byId('kemsDesc').textContent.includes(`모선 ${api.buses.length}`) && byId('kemsDesc').textContent.includes(`선로 ${api.lines.length}`));
    const kvs = [...new Set(api.buses.map((b) => b.voltage_kv).filter(Number.isFinite))].sort((a, b) => b - a);
    check(`${label}: legend lists the API voltage classes`, kvs.slice(0, 3).every((kv) => byId('kemsLegend').textContent.includes(`${kv.toLocaleString('ko-KR')} kV`)), byId('kemsLegend').textContent);
    const shown = document.body.innerText;
    check(`${label}: no demo figure visible`, DEMO_FIGURES.every((figure) => !shown.includes(figure)), DEMO_FIGURES.filter((f) => shown.includes(f)));
  }

  function kpisMatchPf(label, pf) {
    const m = pf.metadata, s = pf.summary, sv = s.screening_violations;
    const load = numbers(tileValue('load'))[0];
    check(`${label}: KPI 총 부하 = metadata.load_mw`, close(load, m.load_mw, Math.abs(m.load_mw) >= 100 ? 0 : 1), [tileValue('load'), m.load_mw]);
    check(`${label}: generation is not invented`, tileNote('load').includes('발전 합계: 응답에 없음'), tileNote('load'));
    const [vmin, vmax] = numbers(tileValue('voltage'));
    check(`${label}: KPI 전압 범위 = summary.voltage_min/max_pu`, close(vmin, s.voltage_min_pu, 3) && close(vmax, s.voltage_max_pu, 3), [tileValue('voltage'), s.voltage_min_pu, s.voltage_max_pu]);
    check(`${label}: KPI 최대 선로 부하율 = summary.line_max_loading_percent`, close(numbers(tileValue('loading'))[0], s.line_max_loading_percent, 1), [tileValue('loading'), s.line_max_loading_percent]);
    const expectedViolations = [sv.bus_voltage, sv.line_thermal, sv.transformer_thermal];
    check(`${label}: KPI 위반 = screening_violations, not summed`, JSON.stringify(numbers(tileValue('violations'))) === JSON.stringify(expectedViolations), [tileValue('violations'), expectedViolations]);
    check(`${label}: PF counts shown apart from map counts`, byId('kemsSide').textContent.includes(`모선 ${pf.buses.length} · 선로 ${pf.lines.length}`), null);
    check(`${label}: PF marked read-only and synthetic`, byId('kemsSide').textContent.includes('조회 전용 · 실시간 아님')
      && (m.synthetic_scenario !== true || byId('kemsSide').textContent.includes('합성 시나리오')));
    const top = [...pf.lines].sort((a, b) => b.loading_percent - a.loading_percent)[0];
    const firstRank = document.querySelector('#kemsSide [data-sec="top"] .kems-rank');
    check(`${label}: top loaded line from PF`, firstRank && firstRank.dataset.line === String(top.line_id), [firstRank && firstRank.dataset.line, top.line_id]);
    const buckets = {over: pf.lines.some((l) => l.loading_percent >= 100 || l.thermal_violation), high: pf.lines.some((l) => l.loading_percent >= 80 && l.loading_percent < 100)};
    check(`${label}: line colours follow PF loading`, !!svg().querySelector('.k-line.k-ld-high') === buckets.high && !!svg().querySelector('.k-line.k-ld-over') === buckets.over, buckets);
    return top;
  }

  async function interactions(label, bus, line, busName, pf) {
    const pfBus = pf.buses.find((b) => String(b.bus_id) === String(bus));
    const pfLine = pf.lines.find((l) => String(l.line_id) === String(line));
    check(`${label}: click selects a line by hit test`, clickFeature('line', line, 4) && byId('kemsCard').dataset.id === String(line) && byId('kemsCard').dataset.kind === 'line', cardText().slice(0, 120));
    byId('zfit').click();
    check(`${label}: line card shows PF loading from the API`, close(numbers(cardValue('PF 부하율'))[0], pfLine.loading_percent, 1), [cardValue('PF 부하율'), pfLine.loading_percent]);
    check(`${label}: line card does not claim analysis`, cardValue('선로 사고 해석') === 'Phase 3 연동 예정 (미연동)');
    check(`${label}: click selects a bus`, clickFeature('bus', bus) && byId('kemsCard').dataset.id === String(bus) && cardText().includes(busName), cardText().slice(0, 120));
    check(`${label}: bus card shows PF |V| from the API`, close(numbers(cardValue('PF |V|'))[0], pfBus.v_mag_pu, 3), [cardValue('PF |V|'), pfBus.v_mag_pu]);
    const q = byId('kemsQ');
    q.value = String(line);
    q.dispatchEvent(new Event('input'));
    q.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter'}));
    check(`${label}: search finds and selects the line`, byId('kemsCard').dataset.id === String(line) && view().selection.id === String(line));
    byId('kemsCard').querySelector('.kems-cardhead .btn').click();
    check(`${label}: card closes`, !visible(byId('kemsCard')) && view().selection === null);

    const host = svg();
    const nodes = host.querySelectorAll('*').length;
    let childChanges = 0;
    const observer = new MutationObserver((records) => { childChanges += records.filter((r) => r.type === 'childList').length; });
    observer.observe(host, {childList: true, subtree: true});
    const rect = host.getBoundingClientRect();
    const before = window.kemsConsole.map.viewBox();
    host.dispatchEvent(new WheelEvent('wheel', {clientX: rect.left + rect.width / 2, clientY: rect.top + rect.height / 2, deltaY: -400, bubbles: true, cancelable: true}));
    const zoomed = window.kemsConsole.map.viewBox();
    pointer('pointerdown', rect.left + 200, rect.top + 150);
    pointer('pointermove', rect.left + 260, rect.top + 190);
    pointer('pointermove', rect.left + 320, rect.top + 230);
    pointer('pointerup', rect.left + 320, rect.top + 230);
    const panned = window.kemsConsole.map.viewBox();
    await tick();
    observer.disconnect();
    check(`${label}: wheel zooms by viewBox only`, zoomed.w < before.w * 0.8, [before.w, zoomed.w]);
    check(`${label}: drag pans by viewBox only`, panned.x < zoomed.x && panned.y < zoomed.y && panned.w === zoomed.w, [zoomed, panned]);
    check(`${label}: zoom/pan never rebuild the DOM`, childChanges === 0 && host.querySelectorAll('*').length === nodes, {childChanges, nodes});
    const sldBox = byId('sld').getAttribute('viewBox');
    byId('zfit').click();
    const fit = window.kemsConsole.map.viewBox();
    byId('zin').click();
    const zin = window.kemsConsole.map.viewBox();
    check(`${label}: Artifact zoom buttons drive the API map`, fit.x === 0 && fit.y === 0 && zin.w < fit.w && byId('sld').getAttribute('viewBox') === sldBox, [fit, zin]);
    byId('zfit').click();
  }

  async function apiScenario() {
    await window.kemsConsole.ready;
    const catalog = await getJson('/api/grids');
    const ids = catalog.grids.map((grid) => grid.id);
    const entry = (id) => catalog.grids.find((grid) => grid.id === id);
    check('three EMS API grids in /api/grids', ['kr-kpg193', 'mn-osm-35k', 'kr-osm-transmission'].every((id) => ids.includes(id)), ids);
    const groups = [...byId('model').querySelectorAll('optgroup')].map((g) => g.label);
    check('select has EMS API and demo groups', groups.length === 2 && groups[0] === 'EMS API 계통' && groups[1].startsWith('Artifact 데모 계통'), groups);
    const options = apiOptions();
    check('API options are /api/grids IDs in order', JSON.stringify(options.map((o) => o.value)) === JSON.stringify(ids.map((id) => `api:${id}`)), options);
    const labels = Object.fromEntries(options.map((o) => [o.value, o.label]));
    check('API option labels', labels['api:kr-kpg193'] === 'KPG-193 (EMS API)' && labels['api:mn-osm-35k'] === '몽골 OSM (EMS API)'
      && labels['api:kr-osm-transmission'] === '한국 OSM (EMS API, 지도 전용)', labels);
    const demo = [...byId('model').querySelectorAll('optgroup')[1].children].map((o) => [o.value, o.textContent]);
    check('demo options keep Artifact IDs', JSON.stringify(demo) === JSON.stringify([['kpg193', 'KPG193 (DC 데모)'], ['ieee39', 'IEEE39 (데모)'], ['ieee14', 'IEEE14 (데모)'], ['six', '가상 6모선 (데모)']]), demo);
    check('starts on the KPG193 demo', state().source === 'demo' && byId('model').value === 'kpg193' && document.body.dataset.kemsSrc === 'demo');
    check('demo figures on load', JSON.stringify(kpis()) === JSON.stringify(DEMO_KPG), kpis());
    check('demo header badge', byId('ctxInfo').querySelector('.kems-src-demo') !== null);
    check('live toggle labelled as simulation', document.querySelector('.mtog button[data-m="live"]').textContent === '실계통 연동 (모의)');
    check('API parts hidden and empty in demo', !visible(byId('kemsMapHost')) && !visible(byId('kemsKpis')) && !visible(byId('kemsSide'))
      && byId('kemsKpis').children.length === 0 && byId('kemsSide').children.length === 0 && window.kemsConsole.map.counts() === null);
    check('demo screens not gated in demo', [...document.querySelectorAll('.kems-gate')].every((g) => !visible(g)));

    countEngineCalls();
    window.__listenerLog = [];
    const messages = byId('msgs').children.length;

    /* KPG-193 */
    const kpgMap = await getJson('/api/grids/kr-kpg193/map');
    const kpgPf = await getJson('/api/grids/kr-kpg193/pf-preview');
    await chooseApi('kr-kpg193');
    engine.calls = 0;
    const kv = view();
    check('KPG-193: catalog, map and PF ready', kv.status === 'ready' && kv.map.status === 'ready' && kv.pf.status === 'ready', kv);
    await mapMatchesApi('kr-kpg193', 'KPG-193', kpgMap);
    check('KPG-193: generators from /map mark their buses', svg().querySelector('.k-bus.k-feed') !== null && byId('kemsDesc').textContent.includes(`발전기 ${kpgMap.generators.length}`));
    const kpgTop = kpisMatchPf('KPG-193', kpgPf);
    check('KPG-193: PF overlay on by default, toggle back to voltage', kv.mode === 'loading' && (() => {
      document.querySelector('#kemsMode button[data-mode="voltage"]').click();
      const plain = !svg().querySelector('.k-line.k-ld-high') && view().mode === 'voltage';
      document.querySelector('#kemsMode button[data-mode="loading"]').click();
      return plain && view().mode === 'loading';
    })());
    check('KPG-193: HVDC outside the AC results is disclosed', byId('kemsSide').textContent.includes('HVDC 2회선'));
    const kpgTarget = clickTargets(kpgMap, kpgPf, kpgTop.line_id);
    await interactions('KPG-193', kpgTarget.bus, kpgTarget.line, kpgTarget.name, kpgPf);
    check('KPG-193: mode toggle and shared analyses disabled', [...document.querySelectorAll('.mtog button')].every((b) => b.disabled) && byId('apBtn').disabled);
    await ask('현재 계통 상태 알려줘');
    await switchMode('live');
    check('KPG-193: demo chat and live switch inert', byId('msgs').children.length === messages && MODE === 'pub');
    for (const v of VIEWS.filter((x) => x !== 'ops')) {
      nav(v);
      await wait(300);
      const section = byId(`v-${v}`);
      const gate = section.querySelector('.kems-gate');
      const others = [...section.children].filter((child) => !child.classList.contains('vhead') && child !== gate);
      check(`KPG-193: ${v} demo screen gated`, visible(gate) && others.every((child) => !visible(child)), others.filter(visible).map((c) => c.className));
    }
    nav('rp');
    role('책임자');  // addJN() -> renderReport() -> dcpf() unless the bridge keeps it idle
    role('관제사');
    byId('menuAll').checked = false;
    byId('menuAll').dispatchEvent(new Event('change'));
    byId('menuAll').checked = true;
    byId('menuAll').dispatchEvent(new Event('change'));
    nav('ops');
    dispatchEvent(new Event('resize'));
    document.dispatchEvent(new KeyboardEvent('keydown', {key: '/', bubbles: true, cancelable: true}));
    check('KPG-193: "/" focuses the API search box', document.activeElement === byId('kemsQ'));
    byId('kemsQ').blur();
    await wait(6000);
    check('KPG-193: DC engine idle (tabs, role change on 보고서, resize)', engine.calls === 0, engine.calls);

    /* Mongolia */
    const mnMap = await getJson('/api/grids/mn-osm-35k/map');
    const mnPf = await getJson('/api/grids/mn-osm-35k/pf-preview');
    const mnGeo = await getJson('/api/grids/mn-osm-35k/display-geometry');
    await chooseApi('mn-osm-35k');
    const mv = view();
    check('몽골: catalog, map, geometry and PF ready', mv.status === 'ready' && mv.map.status === 'ready' && mv.geometry.status === 'ready' && mv.pf.status === 'ready', mv);
    await mapMatchesApi('mn-osm-35k', '몽골', mnMap);
    const parts = mnGeo.lines.reduce((sum, line) => sum + line.osm_parts.length, 0);
    check('몽골: GIS routes from /display-geometry, the rest straight', mv.map.counts.gis === mnGeo.lines.length && mv.geometry.parts === parts
      && mv.map.counts.straightFallback === mnMap.lines.length - mnGeo.lines.length, {view: mv.map.counts, geo: [mnGeo.lines.length, parts]});
    check('몽골: GIS statement in the map header', byId('kemsDesc').textContent.includes(`GIS 경로 ${mnGeo.lines.length}선로 (OSM 조각 ${parts})`)
      && byId('kemsDesc').textContent.includes(`직선 ${mnMap.lines.length - mnGeo.lines.length}선로`) && byId('kemsDesc').textContent.includes('합성 connector 미표시'));
    check('몽골: straight lines drawn dashed and explained', !!svg().querySelector('.k-line.k-straight') && byId('kemsLegend').textContent.includes('직선 (GIS 경로 미적용)'));
    const mnTop = kpisMatchPf('몽골', mnPf);
    check('몽골: map 173 vs PF 166 kept apart', byId('kemsSide').textContent.includes(`모선 ${mnMap.buses.length} · 선로 ${mnMap.lines.length}`)
      && byId('kemsSide').textContent.includes(`모선 ${mnPf.metadata.excluded_bus_ids.length} · 선로 ${mnPf.metadata.excluded_line_ids.length}`));
    check('몽골: excluded lines have no PF colour', mnPf.metadata.excluded_line_ids.length === 0 || !!svg().querySelector('.k-line.k-ld-none'));
    check('몽골: no KPG values left', !byId('kemsSide').textContent.includes('kr-kpg193') && !byId('kemsKpis').textContent.includes('66,135')
      && !byId('kemsDesc').textContent.includes('KPG'));
    const mnTarget = clickTargets(mnMap, mnPf);
    await interactions('몽골', mnTarget.bus, mnTarget.line, mnTarget.bus, mnPf);

    /* Korean OSM */
    const krMap = await getJson('/api/grids/kr-osm-transmission/map');
    await chooseApi('kr-osm-transmission');
    const rv = view();
    check('한국 OSM: map ready, PF unsupported', rv.status === 'ready' && rv.map.status === 'ready' && rv.pf.status === 'unsupported', rv);
    await mapMatchesApi('kr-osm-transmission', '한국 OSM', krMap);
    check('한국 OSM: OSM routes used', krMap.lines.every((line) => Array.isArray(line.coordinates)) && svg().querySelectorAll('.k-line').length > 0);
    check('한국 OSM: PF KPIs unsupported, not filled', ['load', 'voltage', 'loading', 'violations'].every((key) => tileValue(key) === 'N/A' && tileNote(key).includes('미지원')),
      ['load', 'voltage', 'loading', 'violations'].map(tileNote));
    check('한국 OSM: PF colour mode disabled', document.querySelector('#kemsMode button[data-mode="loading"]').disabled && !svg().querySelector('[class*="k-ld-"]'));
    check('한국 OSM: unsupported analysis stated', byId('kemsSide').textContent.includes('미지원 · 이 계통은 지도 전용입니다'));
    // Dense grid: magnify around the longest line before clicking it.
    const longest = [...krMap.lines].sort((a, b) => (b.length_km || 0) - (a.length_km || 0))[0];
    check('한국 OSM: line card shows OSM facts', clickFeature('line', longest.line_id, 8) && byId('kemsCard').dataset.id === longest.line_id
      && cardValue('표시 경로') === 'OSM 경로' && (cardValue('전기 정수') || '').includes('missing'), cardText().slice(0, 160));
    byId('zfit').click();

    const requested = await hits();
    check('reference layers never requested', !requested.some((h) => h.includes('reference-layers')), requested.filter((h) => h.includes('reference')));
    check('no PF request for the map-only grid', !requested.some((h) => h.startsWith('/api/grids/kr-osm-transmission/pf-preview')), requested.filter((h) => h.includes('kr-osm')));
    const persistent = window.__listenerLog.filter(([target]) => ['window', 'document', 'svg', 'kemsMapHost', 'model', 'nav'].includes(target));
    check('grid switches add no listeners to persistent targets', persistent.length === 0, persistent);
    check('API grids never ran the DC engine', engine.calls === 0, engine.calls);

    /* back to the demo */
    nav('db');
    await wait(300);
    document.querySelector('#v-db .kems-gate button').click();
    await wait(2500);
    check('gate button returns to the last demo grid', state().source === 'demo' && byId('model').value === 'kpg193' && JSON.stringify(kpis()) === JSON.stringify(DEMO_KPG), kpis());
    check('dashboard re-rendered for the demo grid', visible(byId('dKpi')) && byId('dKpi').textContent.includes('55,606'), byId('dKpi').textContent.slice(0, 100));
    nav('ops');
    await wait(300);
    check('API map, KPIs and panels cleared after leaving', window.kemsConsole.map.counts() === null && byId('kemsKpis').children.length === 0
      && byId('kemsSide').children.length === 0 && !visible(byId('kemsMapHost')) && visible(byId('sld')) && document.body.dataset.kemsSrc === 'demo');
    check('mode toggle enabled again', [...document.querySelectorAll('.mtog button')].every((b) => !b.disabled) && !byId('apBtn').disabled);
    await chooseApi('kr-kpg193');
    choose('ieee39');
    await wait(2500);
    check('API -> IEEE39 demo loads IEEE39 values', byId('model').value === 'ieee39' && text('kLoad') === '6,254 MW' && text('kN1') === '20건', kpis());
    choose('kpg193');
    await wait(2500);
    const before = byId('msgs').children.length;
    await ask('현재 계통 상태 알려줘');
    await settle();
    check('demo chat works after returning', byId('msgs').children.length === before + 2);
    check('demo engine runs again in demo mode', engine.calls > 0, engine.calls);

    document.querySelector('.mtog button[data-m="live"]').click();
    await wait(4000);
    check('simulated live mode labelled', MODE === 'live' && visible(byId('ctxInfo').querySelector('.kems-src-sim')) && byId('ctxInfo').textContent.includes('실계통 연동 (모의)'));
    await chooseApi('kr-kpg193');
    engine.calls = 0;
    await wait(7000);
    check('live -> API stops the simulated feed', MODE === 'pub' && document.body.dataset.mode === 'pub' && engine.calls === 0, {mode: MODE, calls: engine.calls});
    choose('kpg193');
    await wait(2500);
    check('back to the public demo after live', MODE === 'pub' && byId('model').value === 'kpg193' && text('kLoad') === '55,606 MW', kpis());

    nav('sn');
    await wait(300);
    byId('lkRun').click();
    await wait(1000);
    const lakeOn = LK.on;
    nav('ops');
    await chooseApi('kr-kpg193');
    check('lake collection stopped on API selection', lakeOn && !LK.on);
    choose('kpg193');
    await wait(2500);

    // Demo work in progress blocks the switch (checked synchronously, before the work can finish).
    const refused = (reason) => state().source === 'demo' && byId('model').value === 'kpg193'
      && ((document.querySelector('.toast') || {}).textContent || '').includes(reason);
    nav('ev');
    await wait(300);
    byId('runBench').click();
    choose('api:kr-kpg193');
    check('switch refused while a demo benchmark runs', refused('성능 평가 채점'), (document.querySelector('.toast') || {}).textContent);
    for (let i = 0; i < 600 && byId('runBench').disabled; i++) await wait(500);
    check('benchmark completed on the demo grid', text('bCases') === '365' && text('bViol') === '20건', [text('bCases'), text('bViol')]);
    nav('ops');
    await wait(300);
    const pending = ask('N-1 점검해줘');
    choose('api:kr-kpg193');
    check('switch refused while a demo answer is in progress', refused('데모 응답 처리'));
    await pending;
    await settle();
    byId('trainBtn').click();
    await wait(300);
    choose('api:kr-kpg193');
    check('switch refused during training mode', refused('훈련 모드'));
    byId('clearBtn').click();
    await wait(300);
    check('training cleared, demo intact', !TRN.on && state().source === 'demo' && text('kLoad') === '55,606 MW');
  }

  /* ---------- held responses ---------- */
  async function gate(match, fail = false) {
    return (await (await fetch('/__probe__/gates', {method: 'POST', body: JSON.stringify({match, fail})})).json()).id;
  }
  const requested = (id) => fetch(`/__probe__/gates/${id}?wait=1`).then((r) => r.json());
  const release = (id) => fetch(`/__probe__/gates/${id}/release`, {method: 'POST'});

  async function staleScenario() {
    await window.kemsConsole.ready;
    countEngineCalls();

    // A KPG map that arrives after the switch to Mongolia (different content: 193 vs 173 buses).
    const kpgMap = await gate('=/api/grids/kr-kpg193/map');
    choose('api:kr-kpg193');
    await requested(kpgMap);
    check('pending map: loading shown, no values', view().map.status === 'loading' && visible(byId('kemsMapMsg')) && byId('kemsMapMsg').textContent.includes('불러오는 중')
      && window.kemsConsole.map.counts() === null && [...document.querySelectorAll('#kemsKpis .kpi b')].every((b) => b.textContent === 'N/A') && demoHidden());
    choose('api:mn-osm-35k');
    await until(() => settled('mn-osm-35k'), 'mn ready');
    await release(kpgMap);
    for (let i = 0; i < 30; i++) await tick();
    check('late KPG map dropped after switching to Mongolia', view().gridId === 'mn-osm-35k' && window.kemsConsole.map.counts().buses === 173
      && byId('kemsDesc').textContent.includes('몽골') && byId('model').value === 'api:mn-osm-35k', window.kemsConsole.map.counts());

    // Mongolia PF that arrives after the switch to the map-only Korean grid.
    const mnPf = await gate('=/api/grids/mn-osm-35k/pf-preview');
    choose('api:mn-osm-35k');
    await requested(mnPf);
    await until(() => view().map.status === 'ready', 'mn map');
    check('pending PF: map shown, KPIs wait', view().pf.status === 'loading' && tileNote('load').includes('불러오는 중'));
    choose('api:kr-osm-transmission');
    await until(() => settled('kr-osm-transmission'), 'kr ready');
    await release(mnPf);
    for (let i = 0; i < 30; i++) await tick();
    check('late Mongolia PF dropped on the Korean grid', view().gridId === 'kr-osm-transmission' && view().pf.status === 'unsupported'
      && tileValue('load') === 'N/A' && !svg().querySelector('[class*="k-ld-"]'), [tileValue('load'), view().pf.status]);

    // A failing catalog response for the first grid that arrives after the second grid is shown.
    const oldCatalog = await gate('=/api/grids', true);
    choose('api:kr-kpg193');
    await requested(oldCatalog);
    choose('api:mn-osm-35k');
    await until(() => settled('mn-osm-35k'), 'mn ready again');
    await release(oldCatalog);
    for (let i = 0; i < 30; i++) await tick();
    check('late failed catalog dropped', view().gridId === 'mn-osm-35k' && view().status === 'ready' && state().catalogError === null
      && !visible(document.querySelector('.kems-retry')), state());

    check('DC engine idle while API responses were pending', engine.calls === 0, engine.calls);

    // A late API response after returning to a demo grid never reopens the API view.
    const late = await gate('=/api/grids/kr-kpg193/map');
    choose('api:kr-kpg193');
    await requested(late);
    choose('six');  // no timers while a response is held: Chrome virtual time pauses on pending fetches
    await until(() => state().source === 'demo' && text('kLoad') === '750 MW', 'six loaded');
    await release(late);
    for (let i = 0; i < 30; i++) await tick();
    check('late API response after leaving does not reopen API view', state().source === 'demo' && window.kemsConsole.map.counts() === null
      && byId('kemsKpis').children.length === 0 && byId('model').value === 'six' && text('kLoad') === '750 MW', kpis());
  }

  /* ---------- failures ---------- */
  async function initialFailure(expected) {
    await window.kemsConsole.ready;
    const options = apiOptions();
    check('no API grid offered after a failed catalog', options.length === 1 && options[0].disabled && options[0].value === '', options);
    check(`failure reason shown (${expected})`, options[0].label.includes(expected), options[0].label);
    check('retry button visible', visible(document.querySelector('.kems-retry')));
    check('stays on the demo, no fallback', state().source === 'demo' && state().catalogError && byId('model').value === 'kpg193');
    check('demo unaffected', JSON.stringify(kpis()) === JSON.stringify(DEMO_KPG), kpis());
    const before = (await hits()).filter((h) => h === '/api/grids').length;
    document.querySelector('.kems-retry').click();
    for (let i = 0; i < 40; i++) await tick();
    const after = (await hits()).filter((h) => h === '/api/grids').length;
    check('retry sends a new catalog request and reports the failure again', after === before + 1 && apiOptions()[0].label.includes(expected)
      && visible(document.querySelector('.kems-retry')), [before, after]);
  }

  async function endpointFailure(kind) {
    await window.kemsConsole.ready;
    countEngineCalls();
    const grid = ['geo404', 'geobad'].includes(kind) ? 'mn-osm-35k' : 'kr-kpg193';
    await chooseApi(grid);
    const v = view();
    const shown = document.body.innerText;
    check(`${kind}: no demo figure, demo parts hidden`, DEMO_FIGURES.every((f) => !shown.includes(f)) && demoHidden());
    if (kind === 'pf500' || kind === 'pfgrid' || kind === 'pfnonconv') {
      const status = {pf500: 'error', pfgrid: 'invalid', pfnonconv: 'nonconverged'}[kind];
      const note = {pf500: 'PF 조회 실패 · HTTP 500', pfgrid: 'PF 검증 실패 · PF 응답의 계통 ID(mn-osm-35k)', pfnonconv: 'PF 비수렴'}[kind];
      check(`${kind}: map still shown`, v.map.status === 'ready' && window.kemsConsole.map.counts().buses === 193);
      check(`${kind}: PF ${status}, KPIs without numbers`, v.pf.status === status && ['load', 'voltage', 'loading', 'violations'].every((key) => tileValue(key) === 'N/A' && tileNote(key).includes(note)),
        {status: v.pf.status, notes: ['load', 'voltage', 'loading', 'violations'].map(tileNote)});
      check(`${kind}: no PF colours, toggle disabled`, !svg().querySelector('[class*="k-ld-"]') && document.querySelector('#kemsMode button[data-mode="loading"]').disabled);
      if (kind !== 'pfnonconv') check(`${kind}: side panel says no demo substitute`, byId('kemsSide').textContent.includes('데모 결과로 대체하지 않습니다'));
    } else if (kind === 'map500' || kind === 'mapdrop') {
      const message = kind === 'map500' ? 'HTTP 500' : 'EMS API 서버에 연결할 수 없습니다';
      check(`${kind}: map error shown, nothing drawn`, v.map.status === 'error' && byId('kemsMapMsg').textContent.includes(message)
        && byId('kemsMapMsg').textContent.includes('데모 지도로 대체하지 않습니다') && window.kemsConsole.map.counts() === null, v.map);
      check(`${kind}: PF not shown without its map`, v.pf.status === 'blocked' && tileValue('load') === 'N/A' && tileNote('load').includes('지도 오류'), v.pf);
    } else {
      const reason = kind === 'geo404' ? 'GIS 표시 자료 없음' : 'GIS 표시 자료 검증 실패';
      check(`${kind}: base map kept, every line straight`, v.map.status === 'ready' && v.map.counts.gis === 0 && window.kemsConsole.map.counts().lines === 159
        && !svg().querySelector('.k-line.k-straight'), v);
      check(`${kind}: simplified display stated`, byId('kemsDesc').textContent.includes(reason) && byId('kemsDesc').textContent.includes('모든 선로 직선 표시'), byId('kemsDesc').textContent);
      check(`${kind}: PF unaffected`, v.pf.status === 'ready' && tileValue('load') !== 'N/A');
    }
    check(`${kind}: DC engine idle`, engine.calls === 0, engine.calls);
  }

  async function timeoutScenario() {
    await window.kemsConsole.ready;
    const realFetch = window.fetch;
    window.fetch = (url, options = {}) => (String(url) === '/api/grids'
      ? new Promise((resolve, reject) => options.signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError'))))
      : realFetch(url, options));
    choose('api:kr-kpg193');
    await wait(11000);
    window.fetch = realFetch;
    check('unanswered catalog check times out', view().status === 'error' && byId('kemsMapMsg').textContent.includes('응답 시간 초과 (10초)'), view());
    check('timeout shows no map or values', window.kemsConsole.map.counts() === null && !byId('kemsSide').querySelector('tr[data-flag]')
      && [...document.querySelectorAll('#kemsKpis .kpi em')].every((em) => em.textContent.includes('확인 불가')));
  }

  async function fontsScenario() {
    await wait(3000);
    const family = getComputedStyle(document.body).fontFamily;
    const plex = [...document.fonts].filter((face) => face.family.replace(/"/g, '') === 'IBM Plex Sans KR' && face.status === 'loaded');
    check('web font not loaded', plex.length === 0, plex.length);
    check('unreachable font stylesheet never applied', document.querySelector('link[href*="fonts.googleapis.com/css2"]').media === 'print');
    check('Korean fallback font stack declared', family.includes('Malgun Gothic') && family.includes('Noto Sans KR') && family.includes('system-ui'), family);
    const header = document.querySelector('.top').getBoundingClientRect();
    check('header laid out', header.height > 40 && header.height < 260, header.height);
    check('demo rendered with fallback', text('kLoad') === '55,606 MW' && document.querySelectorAll('#sld *').length > 1000);
    await window.kemsConsole.ready;
    check('EMS API list loaded with fallback', apiOptions().length === 3);
  }

  /* ---------- real-time performance (the test runs this page without virtual time) ---------- */
  async function perfScenario() {
    await window.kemsConsole.ready;
    const out = {grids: {}};
    const median = (values) => { const s = [...values].sort((a, b) => a - b); return s.length ? s[Math.floor(s.length / 2)] : null; };
    for (const id of ['kr-osm-transmission', 'kr-kpg193', 'mn-osm-35k', 'kr-osm-transmission']) {
      performance.clearResourceTimings();
      const t0 = performance.now();
      choose(`api:${id}`);
      await until(() => view() && view().gridId === id && view().map.status === 'ready', `perf ${id}`, 100000);
      await frame();
      const mapReady = performance.now() - t0;
      await until(() => settled(id), `perf ${id} pf`, 100000);
      await frame();
      const allReady = performance.now() - t0;
      const res = performance.getEntriesByType('resource').find((e) => e.name.includes(`/api/grids/${id}/map`));
      const host = svg();
      const rect = host.getBoundingClientRect();
      const zoom = [];
      for (let i = 0; i < 12; i++) {
        const s = performance.now();
        host.dispatchEvent(new WheelEvent('wheel', {clientX: rect.left + rect.width * 0.45, clientY: rect.top + rect.height * 0.4, deltaY: i < 6 ? -300 : 300, bubbles: true, cancelable: true}));
        await frame();
        zoom.push(performance.now() - s);
      }
      const pan = [];
      pointer('pointerdown', rect.left + 300, rect.top + 200);
      for (let i = 1; i <= 15; i++) {
        const s = performance.now();
        pointer('pointermove', rect.left + 300 + i * 12, rect.top + 200 + i * 6);
        await frame();
        pan.push(performance.now() - s);
      }
      pointer('pointerup', rect.left + 480, rect.top + 290);
      out.grids[`${id}${out.grids[id] ? '#2' : ''}`] = {
        mapReadyMs: Math.round(mapReady), allReadyMs: Math.round(allReady),
        mapFetchMs: res ? Math.round(res.responseEnd - res.startTime) : null, mapBytes: res ? res.encodedBodySize || res.transferSize : null,
        renderAfterFetchMs: res ? Math.round(mapReady - (res.responseEnd - t0)) : null,
        zoomFrameMs: {median: Math.round(median(zoom)), max: Math.round(Math.max(...zoom))},
        panFrameMs: {median: Math.round(median(pan)), max: Math.round(Math.max(...pan))},
        nodes: window.kemsConsole.map.counts().nodes,
      };
    }
    const kr = out.grids['kr-osm-transmission'];
    check('perf: Korean OSM map renders within 3 s after its JSON arrived', kr.renderAfterFetchMs !== null && kr.renderAfterFetchMs < 3000, kr);
    check('perf: Korean OSM zoom frame median < 50 ms', kr.zoomFrameMs.median < 50, kr.zoomFrameMs);
    check('perf: Korean OSM pan frame median < 50 ms', kr.panFrameMs.median < 50, kr.panFrameMs);
    check('perf: grid switches keep the DOM small', Object.values(out.grids).every((g) => g.nodes < 80), Object.values(out.grids).map((g) => g.nodes));
    check('perf: second Korean load not slower than 2x the first', out.grids['kr-osm-transmission#2'].renderAfterFetchMs < 2 * kr.renderAfterFetchMs + 200, out.grids['kr-osm-transmission#2']);
    return out;
  }

  async function shotScenario(name) {
    // Screenshot the settled state: app.css fades each screen child in (.view.on>*), and headless
    // capture can land mid-fade.
    const still = document.createElement('style');
    still.textContent = '.view.on>*,.msgs>*{animation:none!important}';
    document.head.append(still);
    await window.kemsConsole.ready;
    const grid = {'api-kpg': 'kr-kpg193', 'api-kpg-pf': 'kr-kpg193', 'api-mn': 'mn-osm-35k', 'api-mn-pf': 'mn-osm-35k', 'api-kr': 'kr-osm-transmission',
      'api-gate': 'kr-kpg193', 'api-error': 'kr-kpg193', 'api-pf-error': 'kr-kpg193', 'api-gis-fallback': 'mn-osm-35k'}[name];
    if (grid) await chooseApi(grid);
    if (['api-kpg', 'api-mn'].includes(name)) document.querySelector('#kemsMode button[data-mode="voltage"]').click();
    if (['api-kpg-pf', 'api-mn-pf'].includes(name)) {
      const first = document.querySelector('#kemsSide [data-sec="top"] .kems-rank');
      if (first) first.click();  // the 부하율 상위 선로 list selects and zooms to the line
    }
    if (name === 'api-gate') nav('db');
    if (name === 'live') { document.querySelector('.mtog button[data-m="live"]').click(); await wait(4000); }
    if (name === 'ieee39') { choose('ieee39'); await wait(2500); }
    await wait(1500);
  }

  try {
    if (mode === 'parity') return finish({fingerprint: await parity(), diagnostics});
    if (mode === 'perf') {
      const timings = await perfScenario();
      finish({timings});
      await fetch('/__probe__/release-load', {method: 'POST'});  // lets the held image (and the load event) finish
      return undefined;
    }
    if (mode === 'api') await apiScenario();
    if (mode === 'stale') await staleScenario();
    if (mode === 'fail500') await initialFailure('HTTP 500');
    if (mode === 'drop') await initialFailure('EMS API 서버에 연결할 수 없습니다');
    if (mode === 'badjson') await initialFailure('응답 형식 오류');
    if (['pf500', 'map500', 'mapdrop', 'geo404', 'geobad', 'pfgrid', 'pfnonconv'].includes(mode)) await endpointFailure(mode);
    if (mode === 'timeout') await timeoutScenario();
    if (mode === 'fonts') await fontsScenario();
    if (mode === 'shot') await shotScenario(params.get('shot'));
  } catch (error) {
    check('probe exception', false, String((error && error.stack) || error));
  }
  finish();
  if (mode === 'perf') await fetch('/__probe__/release-load', {method: 'POST'});
})();
