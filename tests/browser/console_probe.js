/* Browser probe for the Artifact console (/console/) and, in parity mode, the original Artifact.
 * Injected by tests/test_console_browser.py after the page scripts; the report is written to a
 * hidden pre element (id __probe_result). Modes (?probe=):
 *   parity    demo fingerprint (same steps on the original Artifact and on /console/)
 *   api       EMS API grids: real maps, PF preview KPIs/colours, selection, zoom, demo isolation
 *   stale     held map/PF/catalog responses released after a grid switch (test proxy gates)
 *   fail500 | drop | badjson   initial GET /api/grids failure (forced by the test proxy)
 *   pf500 | map500 | mapdrop | geo404 | geobad | pfgrid | pfnonconv   per-endpoint failures (?scenario=)
 *   timeout   GET /api/grids that never answers (fetch stand-in honouring the abort signal)
 *   analysis  N-1 Security Analysis and Sensitivity Analysis on the real EMS API (KPG, Mongolia, Korean OSM)
 *   analysis-stale    held N-1/Sensitivity responses released after a grid or outage change
 *   analysis-monitored   monitored-line changes: Sensitivity cleared/aborted, held answers never reach the new choice
 *   analysis-errors   HTTP errors, network errors, invalid and simulated (labelled 모의) responses, flags off
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
  // Idempotent probe GETs retry a transient network failure (Windows Chrome reaches the WSL proxy through
  // localhost forwarding); no timer in between: virtual time pauses while a held response is pending.
  async function probeGet(path) {
    for (let attempt = 1; ; attempt++) {
      try { return await fetch(path, {cache: 'no-store'}); } catch (error) { if (attempt === 3) throw error; }
    }
  }
  const tick = () => probeGet('/__probe__/tick');
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
  const hits = async () => (await (await probeGet('/__probe__/hits')).json()).hits.filter((h) => !h.includes('__probe='));
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
    const run = byId('kemsCard').querySelector('.kems-an-run');
    check(`${label}: line card offers N-1 for this line, nothing run on selection`, run && run.dataset.line === String(line) && !run.disabled
      && run.textContent === 'N-1 분석 실행' && view().analysis.security.status === 'idle', cardText().slice(-160));
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

  /* ---------- Phase 3: N-1 Security Analysis and Sensitivity Analysis ---------- */
  const an = () => view().analysis;
  const n1Sec = () => document.querySelector('#kemsSide [data-sec="n1"]');
  const sensSec = () => document.querySelector('#kemsSide [data-sec="sens"]');
  const fact = (root, key) => {
    const found = root && [...root.querySelectorAll('.kems-kv')].find((r) => r.querySelector('span').textContent === key);
    return found ? found.querySelector('b').textContent : null;
  };
  // Every number shown, with its decimals: a value matches when it rounds to one of them.
  const shownNumbers = (value) => (String(value).match(/-?\d[\d,]*(\.\d+)?/g) || []).map((t) => ({v: Number(t.replace(/,/g, '')), d: (t.split('.')[1] || '').length}));
  const shows = (value, expected) => expected === null || expected === undefined || shownNumbers(value).some((n) => Math.abs(n.v - expected) <= 0.5 * 10 ** -n.d + 1e-9);
  const apiCall = async (path, params) => {
    const response = await fetch(`${path}?${new URLSearchParams({...params, __probe: '1'})}`, {cache: 'no-store'});
    return {status: response.status, body: await response.json().catch(() => null)};
  };
  const analysisHits = async () => (await hits()).filter((h) => /security-analysis|sensitivity-analysis/.test(h));
  const overlay = () => window.kemsConsole.map.overlay();
  const cardRun = () => byId('kemsCard').querySelector('.kems-an-run');
  const sensRun = () => sensSec().querySelector('[data-action="sensitivity"]');
  const monitorSelect = () => sensSec().querySelector('select.kems-an-mon');
  const pills = (root) => [...root.querySelectorAll('.kems-pill')].map((p) => p.textContent);
  async function rule(spec) {
    return (await (await fetch('/__probe__/rules', {method: 'POST', body: JSON.stringify(spec)})).json()).id;
  }
  const clearRules = () => fetch('/__probe__/rules/clear', {method: 'POST'});

  // Through the search box, as an operator would: the exact line among the hits.
  function selectLine(id) {
    const q = byId('kemsQ');
    q.value = id;
    q.dispatchEvent(new Event('input'));
    const hit = [...byId('kemsRes').querySelectorAll('.msr')].find((b) => b.querySelector('b').textContent === `선로 ${id}`);
    if (hit) hit.dispatchEvent(new MouseEvent('mousedown', {bubbles: true, cancelable: true}));
    byId('zfit').click();
    return !!hit && byId('kemsCard').dataset.id === id && byId('kemsCard').dataset.kind === 'line';
  }
  async function runN1(id) {
    const selected = selectLine(id);
    if (!selected) { check(`select line ${id}`, false, cardText().slice(0, 80)); return false; }
    cardRun().click();
    return until(() => an().line === id && an().security.status !== 'loading', `N-1 ${id}`);
  }
  async function runSensitivity(monitored) {
    if (monitored !== undefined) {
      monitorSelect().value = monitored;
      monitorSelect().dispatchEvent(new Event('change'));
    }
    sensRun().click();
    return until(() => !['idle', 'loading'].includes(an().sensitivity.status), `Sensitivity ${monitored || 'auto'}`);
  }

  const kpgTarget = (map) => {
    const lines = new Set(map.lines.map((l) => String(l.line_id)));
    const buses = new Set(map.buses.map((b) => String(b.bus_id)));
    return (v) => {
      if (lines.has(v.equipment_id)) return ['line', v.equipment_id];
      const level = /^VL-(\d+)$/.exec(v.equipment_id);
      return level && buses.has(level[1]) ? ['bus', level[1]] : [null, v.equipment_id];
    };
  };
  const mnTarget = (v) => [{Line: 'line', Bus: 'bus', Transformer: 'transformer'}[v.equipment_type] || null, v.equipment_id];

  function violationsMatch(label, list, target) {
    const rows = [...n1Sec().querySelectorAll('[data-list="violations"] > *')];
    const bad = list.filter((v, i) => {
      const item = rows[i];
      if (!item) return true;
      const [kind, id] = target(v);
      const values = item.querySelector('i').textContent;
      return item.dataset.id !== id || (kind && item.dataset.kind !== kind) || (v.unit && !values.includes(v.unit))
        || ![v.limit, v.value, v.loading_percent, v.violation_amount].every((x) => shows(values, x));
    });
    check(`${label}: every violation with its limit and post-contingency value`, rows.length === list.length && bad.length === 0,
      {rows: rows.length, api: list.length, bad: bad.slice(0, 3)});
  }

  function candidatesMatch(label, list) {
    const rows = [...sensSec().querySelectorAll('[data-list="candidates"] tr[data-generator]')];
    const bad = list.filter((c, i) => {
      const item = rows[i];
      const value = item && item.querySelector('.kems-an-val b').textContent;
      return !item || item.dataset.generator !== c.generator_id || item.dataset.rank !== String(i + 1) || !shows(value, c.sensitivity)
        || Math.sign(Number(value)) !== Math.sign(c.sensitivity);
    });
    check(`${label}: candidates in the API order with their signed sensitivities`, rows.length === list.length && bad.length === 0,
      {rows: rows.length, api: list.length, bad: bad.slice(0, 2)});
  }

  function meaningStated(label, unit) {
    const items = [...sensSec().querySelectorAll('[data-list="meaning"] li')].map((li) => li.textContent);
    check(`${label}: sign meaning, |rank| is not the effect, no control action`, items.some((t) => t.includes('+ 는') && t.includes('− 는') && t.includes(unit))
      && items.some((t) => t.includes('순위는 |민감도| 크기 순이며 조치 효과·조정 가능량·비용을 뜻하지 않습니다'))
      && items.some((t) => t.includes('재급전을 실행하거나 권고하지 않으며 실제 제어는 수행되지 않았습니다'))
      && pills(sensSec()).includes('소신호 민감도 · 재급전 실행 아님'), items);
  }

  async function analysisScenario() {
    await window.kemsConsole.ready;
    countEngineCalls();

    /* KPG-193: violations, a user-chosen monitored line, non-convergence and NO_OVERLOAD */
    const kpgMap = await getJson('/api/grids/kr-kpg193/map');
    const kTarget = kpgTarget(kpgMap);
    const busName = (map, id) => { const bus = map.buses.find((b) => String(b.bus_id) === String(id)); return bus.name_korean && bus.name_korean !== String(id) ? `${bus.name_korean} (${id})` : String(id); };
    await chooseApi('kr-kpg193');
    engine.calls = 0;
    check('KPG: analysis sections idle before any run', n1Sec().dataset.status === 'idle' && sensSec().dataset.status === 'idle' && sensRun().disabled && overlay() === null
      && document.querySelector('#kemsMode button[data-mode="analysis"]').disabled);
    check('KPG: line card shows the line, its buses and the N-1 button', selectLine('LINE-16-28') && !cardRun().disabled
      && cardValue('구간') === `${busName(kpgMap, 16)} — ${busName(kpgMap, 28)}` && an().security.status === 'idle', cardText().slice(0, 200));
    const before = (await analysisHits()).length;
    check('KPG: selecting a line sends no analysis request', before === 0, before);

    const kRef = (await apiCall('/api/security-analysis', {outage_line_id: 'LINE-16-28', grid_id: 'kr-kpg193'})).body;
    await runN1('LINE-16-28');
    const kRequests = await analysisHits();
    check('KPG N-1: one request to the KPG endpoint with grid_id', kRequests.length === 1 && kRequests[0] === '/api/security-analysis?outage_line_id=LINE-16-28&grid_id=kr-kpg193', kRequests);
    check('KPG N-1: response validated and kept', an().security.status === 'ready' && an().security.verdict === 'violations' && an().security.count === kRef.violated_equipment_count, an().security);
    const k1 = n1Sec();
    check('KPG N-1: statuses and count exactly as returned', fact(k1, '사후 PF') === kRef.post_status && fact(k1, '해석 상태') === kRef.analysis_status
      && fact(k1, '위반 설비 수') === `${kRef.violated_equipment_count}건` && fact(k1, '사전 (기준) PF') === '수렴 (base_converged)'
      && fact(k1, '사고 선로').startsWith(`LINE-16-28 · ${busName(kpgMap, 16)} — ${busName(kpgMap, 28)}`), [...k1.querySelectorAll('.kems-kv')].map((r) => r.textContent));
    violationsMatch('KPG N-1', kRef.violations, kTarget);
    check('KPG N-1: islanding and post-contingency extremes are not in the KPG response, none shown', fact(k1, '계통 분리') === 'KPG 응답에 없음'
      && fact(k1, '사고 후 최대 선로 부하율') === null && !k1.querySelector('[data-list="flows"]') && !k1.querySelector('[data-list="island"]'));
    let drawn = overlay();
    check('KPG N-1: outage line and violated line highlighted', drawn && JSON.stringify(drawn.outage) === '["LINE-16-28"]'
      && JSON.stringify(drawn.violationLines) === JSON.stringify(kRef.violations.map((v) => v.equipment_id)) && visible(svg().querySelector('.k-an-outage'))
      && visible(svg().querySelector('.k-an-viol')) && svg().querySelector('.k-an-outtext').textContent === '사고', drawn);
    check('KPG N-1: base PF colours never painted as post-contingency', view().mode === 'analysis' && !svg().querySelector('[class*="k-ld-"]') && !svg().querySelector('.k-alert')
      && byId('kemsLegend').textContent.includes('다른 선로의 사고 후 상태는 응답에 없어 칠하지 않음') && byId('kemsLegend').textContent.includes('사고 후 위반 선로'), byId('kemsLegend').textContent);
    check('KPG N-1: KPI tiles marked as the base PF', ['load', 'voltage', 'loading', 'violations'].every((key) => tile(key).querySelector('span').textContent.endsWith('· 사고 전')));
    check('KPG N-1: card summarises the same result', byId('kemsCard').querySelector('.kems-an-stat').textContent.startsWith(`위반 ${kRef.violated_equipment_count}건 · 사후 ${kRef.post_status}`)
      && cardRun().textContent === 'N-1 다시 실행');
    k1.querySelector('[data-list="violations"] .kems-rank').click();
    check('KPG N-1: a violation row selects that line, its card ties it to the result', byId('kemsCard').dataset.id === kRef.violations[0].equipment_id
      && (cardValue('N-1 위반 (LINE-16-28 사고)') || '').includes(kRef.violations[0].limit_type), cardText().slice(0, 240));
    byId('zfit').click();
    document.querySelector('#kemsMode button[data-mode="loading"]').click();
    check('KPG N-1: PF mode hides the N-1 overlay, colours are the PF preview again', overlay() === null && !svg().querySelector('.k-an') && !!svg().querySelector('[class*="k-ld-"]')
      && byId('kemsLegend').textContent.includes('색: PF 미리보기'));
    document.querySelector('#kemsMode button[data-mode="analysis"]').click();
    check('KPG N-1: analysis mode restores the overlay without the PF colours', overlay() !== null && !svg().querySelector('[class*="k-ld-"]'));

    const kSens = (await apiCall('/api/sensitivity-analysis', {outage_line_id: 'LINE-16-28', top_n: '5', grid_id: 'kr-kpg193'})).body;
    const choices = [...monitorSelect().options].map((o) => o.value);
    check('KPG sensitivity: offered after the N-1 result, EMS API selection first, violated lines selectable', sensSec().dataset.status === 'idle' && !sensRun().disabled
      && JSON.stringify(choices) === JSON.stringify(['', ...new Set(kRef.violations.map((v) => v.equipment_id))]) && monitorSelect().value === '', choices);
    await runSensitivity('');
    const ks = sensSec();
    check('KPG sensitivity: completed for the line the API selected', an().sensitivity.status === 'ready' && an().sensitivity.state === 'completed'
      && an().sensitivity.monitored === kSens.monitored_line_id && an().sensitivity.requested === null && fact(ks, '선정') === 'EMS API 자동 선정', an().sensitivity);
    check('KPG sensitivity: request without monitored_line_id, top_n 5', (await analysisHits()).pop() === '/api/sensitivity-analysis?outage_line_id=LINE-16-28&top_n=5&grid_id=kr-kpg193');
    candidatesMatch('KPG sensitivity', kSens.candidates);
    check('KPG sensitivity: unit MW/MW kept and its source stated (no unit field in the KPG response)',
      fact(ks, '단위') === 'MW/MW (KPG 응답에 단위 필드 없음 · PyPowSyBl 유효전력 branch flow factor 정의)' && !('unit' in kSens), fact(ks, '단위'));
    meaningStated('KPG sensitivity', 'MW/MW');
    drawn = overlay();
    const candidateBuses = [...new Set(kSens.candidates.map((c) => String(c.bus_id)))];
    check('KPG sensitivity: monitored line and candidate buses on the map', JSON.stringify(drawn.monitored) === JSON.stringify([kSens.monitored_line_id])
      && JSON.stringify([...drawn.candidates].sort()) === JSON.stringify([...candidateBuses].sort()) && visible(svg().querySelector('.k-an-cand'))
      && svg().querySelectorAll('.k-an-rank').length === candidateBuses.length, drawn);
    ks.querySelector('.kems-an-bus').click();
    check('KPG sensitivity: a candidate selects its bus, the card lists it', byId('kemsCard').dataset.kind === 'bus' && byId('kemsCard').dataset.id === String(kSens.candidates[0].bus_id)
      && (cardValue('민감도 후보') || '').startsWith(`1. ${kSens.candidates[0].generator_id}`), cardText().slice(0, 200));
    byId('zfit').click();

    const multi = (await apiCall('/api/security-analysis', {outage_line_id: 'LINE-80-82', grid_id: 'kr-kpg193'})).body;
    await runN1('LINE-80-82');
    check('KPG N-1 (LINE-80-82): the new outage dropped the earlier N-1 and Sensitivity', an().line === 'LINE-80-82' && an().sensitivity.status === 'idle'
      && overlay().monitored.length === 0 && overlay().candidates.length === 0 && !sensSec().querySelector('[data-list="candidates"]'));
    violationsMatch('KPG N-1 (LINE-80-82)', multi.violations, kTarget);
    const lineViolations = multi.violations.filter((v) => kTarget(v)[0] === 'line').map((v) => v.equipment_id);
    const busViolations = multi.violations.filter((v) => kTarget(v)[0] === 'bus').map((v) => kTarget(v)[1]);
    drawn = overlay();
    check('KPG N-1 (LINE-80-82): violated lines and voltage-level buses highlighted', lineViolations.length > 1 && busViolations.length > 1
      && JSON.stringify(drawn.violationLines) === JSON.stringify(lineViolations) && JSON.stringify(drawn.violationBuses) === JSON.stringify(busViolations)
      && visible(svg().querySelector('.k-an-violdot')), {drawn, lineViolations, busViolations});
    const chosen = lineViolations[1];
    const kUser = (await apiCall('/api/sensitivity-analysis', {outage_line_id: 'LINE-80-82', monitored_line_id: chosen, top_n: '5', grid_id: 'kr-kpg193'})).body;
    document.querySelector('#kemsMode button[data-mode="loading"]').click();
    await runSensitivity(chosen);
    check('KPG sensitivity (user): the chosen monitored line was requested and returned', an().sensitivity.requested === chosen && an().sensitivity.monitored === chosen
      && fact(sensSec(), '선정') === '사용자 선택' && (await analysisHits()).pop() === `/api/sensitivity-analysis?outage_line_id=LINE-80-82&monitored_line_id=${chosen}&top_n=5&grid_id=kr-kpg193`, an().sensitivity);
    check('KPG sensitivity (user): its result is drawn even when run from the PF view', view().mode === 'analysis' && overlay().monitored[0] === chosen
      && !svg().querySelector('[class*="k-ld-"]'), {mode: view().mode, drawn: overlay()});
    candidatesMatch('KPG sensitivity (user)', kUser.candidates);

    const nonconv = (await apiCall('/api/security-analysis', {outage_line_id: 'LINE-28-80', grid_id: 'kr-kpg193'})).body;
    await runN1('LINE-28-80');
    const k2 = n1Sec();
    check('KPG N-1 (LINE-28-80): a non-converged contingency is 평가 불가, never 0건', nonconv.post_status !== 'CONVERGED' && an().security.verdict === 'not_assessed'
      && an().security.count === null && fact(k2, '위반 설비 수').startsWith('평가 불가') && fact(k2, '사후 PF') === nonconv.post_status
      && pills(k2)[0] === `평가 불가 · 사고 후 PF ${nonconv.post_status}` && !/위반 0건|위반 없음/.test(k2.textContent), [...k2.querySelectorAll('.kems-kv')].map((r) => r.textContent));
    check('KPG N-1 (LINE-28-80): only the outage line on the map', JSON.stringify(overlay().outage) === '["LINE-28-80"]' && overlay().violationLines.length === 0 && !svg().querySelector('.k-an-viol'));
    check('KPG N-1 (LINE-28-80): card does not claim a result', byId('kemsCard').querySelector('.kems-an-stat').textContent.startsWith('평가 불가'));
    await runSensitivity('');
    check('KPG sensitivity (LINE-28-80): SECURITY_NOT_CONVERGED kept apart from a completed ranking', an().sensitivity.analysisStatus === 'SECURITY_NOT_CONVERGED'
      && an().sensitivity.state === 'not_converged' && !sensSec().querySelector('[data-list="candidates"]') && pills(sensSec())[0] === '미평가 · 사고 후 PF 비수렴'
      && sensSec().textContent.includes('후보 순위를 표시하지 않습니다') && overlay().candidates.length === 0);

    await runN1('LINE-1-2');
    check('KPG N-1 (LINE-1-2): converged without violations', an().security.verdict === 'secure' && fact(n1Sec(), '위반 설비 수') === '0건' && pills(n1Sec())[0] === '위반 없음');
    await runSensitivity('');
    check('KPG sensitivity (LINE-1-2): NO_OVERLOAD is not a completed ranking', an().sensitivity.analysisStatus === 'NO_OVERLOAD' && an().sensitivity.state === 'no_target'
      && !sensSec().querySelector('[data-list="candidates"]') && pills(sensSec())[0] === '감시 대상 없음 · 사고 후 과부하 선로 없음' && !pills(sensSec()).includes('계산 완료'));
    check('KPG: DC engine idle through every analysis', engine.calls === 0, engine.calls);

    /* Mongolia: islanding, flow changes, the API's own monitored-line rule and unit */
    const mnMap = await getJson('/api/grids/mn-osm-35k/map');
    await chooseApi('mn-osm-35k');
    check('grid switch: the KPG result is gone', an().line === null && an().security.status === 'idle' && overlay() === null && n1Sec().dataset.status === 'idle'
      && !byId('kemsSide').textContent.includes('LINE-1-2') && !byId('kemsSide').textContent.includes('NO_OVERLOAD'));
    const island = '222087807-1_0';
    const mRef = (await apiCall('/api/grids/mn-osm-35k/security-analysis', {outage_line_id: island})).body;
    await runN1(island);
    const m1 = n1Sec();
    check('몽골 N-1: request to the Mongolia endpoint', (await analysisHits()).pop() === `/api/grids/mn-osm-35k/security-analysis?outage_line_id=${island}`);
    check('몽골 N-1: islanding from the response', mRef.island_detected && an().security.island.detected && an().security.island.buses === mRef.isolated_bus_count
      && fact(m1, '계통 분리').startsWith(`있음 · 분리 모선 ${mRef.isolated_bus_count}`) && shows(fact(m1, '계통 분리'), mRef.isolated_load_mw), fact(m1, '계통 분리'));
    const islandFacts = m1.querySelector('[data-list="island"]');
    check('몽골 N-1: isolated buses listed, island PF not computed', fact(islandFacts, '분리 모선') === mRef.isolated_buses.join(' ')
      && fact(islandFacts, '분리 섬 PF') === '미실행 (전압·조류 결과 없음)' && fact(islandFacts, '분리 합성 발전기') === (mRef.isolated_generator_ids.join(' ') || '없음'));
    check('몽골 N-1: post-contingency max loading and flow changes from the response', shows(fact(m1, '사고 후 최대 선로 부하율'), mRef.post_max_line_loading_percent)
      && fact(m1, '사고 후 최대 선로 부하율').includes(mRef.post_max_line_loading_id) && shows(fact(m1, '최대 선로 P 변화'), mRef.max_line_p_change_mw)
      && fact(m1, '최대 선로 P 변화').includes(mRef.max_line_p_change_id) && shows(fact(m1, '최대 선로 Q 변화'), mRef.max_line_q_change_mvar)
      && shows(fact(m1, '사고 후 전압 범위'), mRef.post_voltage_min_pu) && shows(fact(m1, '사고 후 전압 범위'), mRef.post_voltage_max_pu));
    const flowRows = [...m1.querySelectorAll('[data-list="flows"] .kems-rank')];
    check('몽골 N-1: flow-change list is the API top list', flowRows.length === mRef.top_line_flow_changes.length && mRef.top_line_flow_changes.every((c, i) => flowRows[i].dataset.id === c.line_id
      && shows(flowRows[i].textContent, c.dp_mw) && shows(flowRows[i].textContent, c.post_loading_percent)), flowRows.map((r) => r.dataset.id));
    check('몽골 N-1: violation count is the main component only, synthetic scenario labelled', fact(m1, '위반 설비 수') === `${mRef.violated_equipment_count}건 (주 계통 기준)`
      && pills(m1).includes('합성 시나리오') && pills(m1).includes('실운영 데이터 아님') && m1.textContent.includes('실제 몽골 운영 데이터가 아닙니다'));
    drawn = overlay();
    check('몽골 N-1: outage, isolated buses and island lines highlighted', JSON.stringify(drawn.outage) === `["${island}"]`
      && JSON.stringify(drawn.isolatedBuses) === JSON.stringify(mRef.isolated_buses) && JSON.stringify(drawn.isolatedLines) === JSON.stringify(mRef.isolated_line_ids)
      && visible(svg().querySelector('.k-an-isobus')) && byId('kemsLegend').textContent.includes('분리 모선 (섬 PF 미계산)'), drawn);
    check('몽골 N-1: an island line card says so', selectLine(mRef.isolated_line_ids[0]) && cardValue('N-1 계통 분리') === `${island} 사고 시 분리 섬 내부 선로`, cardText().slice(0, 200));
    await runSensitivity('');
    check('몽골 sensitivity (islanding): NOT_EVALUATED_ISLANDING, no ranking', an().sensitivity.analysisStatus === 'NOT_EVALUATED_ISLANDING' && an().sensitivity.state === 'not_evaluated'
      && !sensSec().querySelector('[data-list="candidates"]') && pills(sensSec())[0] === '미평가 · 계통 분리 사고' && fact(sensSec(), '분리 모선') === mRef.isolated_buses.join(' ')
      && overlay().candidates.length === 0, an().sensitivity);

    const mesh = '751261406-1_0';
    const m2 = (await apiCall('/api/grids/mn-osm-35k/security-analysis', {outage_line_id: mesh})).body;
    await runN1(mesh);
    check('몽골 N-1 (meshed): no islanding, no violation', !an().security.island.detected && fact(n1Sec(), '계통 분리') === '없음' && an().security.verdict === 'secure'
      && overlay().isolatedBuses.length === 0);
    const mOptions = [...monitorSelect().options].map((o) => o.value);
    check('몽골 sensitivity: selectable monitored lines are the API top flow changes', JSON.stringify(mOptions) === JSON.stringify(['', ...m2.top_line_flow_changes.map((c) => c.line_id)]), mOptions);
    const mS = (await apiCall('/api/grids/mn-osm-35k/sensitivity-analysis', {outage_line_id: mesh, top_n: '5'})).body;
    await runSensitivity('');
    const s1 = sensSec();
    check('몽골 sensitivity: monitored line chosen by the API rule', an().sensitivity.monitored === mS.monitored_line_id
      && fact(s1, '선정') === `EMS API 자동 선정 · ${mS.monitored_line_selection.rule}`, fact(s1, '선정'));
    candidatesMatch('몽골 sensitivity', mS.candidates);
    check('몽골 sensitivity: unit from the response', mS.unit === 'MW/MW' && fact(s1, '단위') === 'MW/MW (EMS API 응답 unit)');
    meaningStated('몽골 sensitivity', 'MW/MW');
    check('몽골 sensitivity: definition, verification and synthetic generators stated', fact(s1, '함수') === mS.function && fact(s1, '유한차분 검증').startsWith('통과')
      && fact(s1, '유효전력 균형') === mS.slack_balance && s1.textContent.includes('합성 발전기 · 실제 몽골 발전소·재급전 후보가 아닙니다'));
    const mBuses = new Set(mnMap.buses.map((b) => String(b.bus_id)));
    check('몽골 sensitivity: candidates linked only through buses on the map', mS.candidates.every((c) => mBuses.has(c.bus_id))
      && JSON.stringify([...overlay().candidates].sort()) === JSON.stringify([...new Set(mS.candidates.map((c) => c.bus_id))].sort()));
    const alt = mS.monitored_line_selection.alternatives[0].line_id;
    const sentBefore = (await analysisHits()).length;
    s1.querySelector(`[data-list="alternatives"] [data-line="${alt}"]`).click();
    check('몽골 sensitivity: an alternative becomes the next monitored line and clears the earlier result', an().monitored === alt && monitorSelect().value === alt
      && an().sensitivity.status === 'idle' && sensSec().dataset.status === 'idle' && !sensSec().querySelector('[data-list="candidates"]')
      && !sensSec().querySelector('[data-list="alternatives"]') && overlay().monitored.length === 0 && overlay().candidates.length === 0
      && !svg().querySelector('.k-an-mon') && !svg().querySelector('.k-an-cand'), {analysis: an(), drawn: overlay()});
    check('몽골 sensitivity: the N-1 result and outage highlight stay, the choice runs nothing', an().security.status === 'ready' && an().line === mesh
      && JSON.stringify(overlay().outage) === JSON.stringify([mesh]) && visible(svg().querySelector('.k-an-outage')) && (await analysisHits()).length === sentBefore);
    const mU = (await apiCall('/api/grids/mn-osm-35k/sensitivity-analysis', {outage_line_id: mesh, monitored_line_id: alt, top_n: '5'})).body;
    await runSensitivity();
    check('몽골 sensitivity (user): the chosen line, user selection and radial note', an().sensitivity.monitored === alt && fact(sensSec(), '선정') === '사용자 선택'
      && fact(sensSec(), '사고 후 방사형').startsWith(mU.monitored_line_radial_after_contingency ? '예' : '아니오') && overlay().monitored[0] === alt);
    candidatesMatch('몽골 sensitivity (user)', mU.candidates);
    const excluded = (await getJson('/api/grids/mn-osm-35k/pf-preview')).metadata.excluded_line_ids[0];
    check('몽골: a PF-excluded line cannot be run (pf-preview excluded_line_ids)', selectLine(String(excluded)) && cardRun().disabled
      && cardText().includes('PF 해석 제외 선로 · EMS API N-1 대상이 아닙니다'), cardText().slice(-160));

    /* Korean OSM: map only, never an analysis request */
    const sent = (await analysisHits()).length;
    const krMap = await getJson('/api/grids/kr-osm-transmission/map');
    await chooseApi('kr-osm-transmission');
    check('한국 OSM: both sections say unsupported, from the catalog flags', n1Sec().dataset.status === 'unsupported' && sensSec().dataset.status === 'unsupported'
      && n1Sec().textContent.includes('security_analysis_available = false') && sensSec().textContent.includes('sensitivity_analysis_available = false'));
    check('한국 OSM: the line card button is disabled with the reason', selectLine(String(krMap.lines[0].line_id)) && cardRun().disabled
      && cardText().includes('미지원 · EMS API security_analysis_available = false'), cardText().slice(-160));
    cardRun().click();
    for (let i = 0; i < 20; i++) await tick();
    const after = await analysisHits();
    check('한국 OSM: no analysis request, no result', after.length === sent && !after.some((h) => h.includes('kr-osm')) && an().security.status === 'idle', after.slice(sent));
    check('한국 OSM: no analysis mode, header says unsupported', document.querySelector('#kemsMode button[data-mode="analysis"]').disabled
      && byId('ctxInfo').textContent.includes('해석 미지원'));
    check('Korean/Mongolian/KPG analyses never ran the DC engine', engine.calls === 0, engine.calls);

    /* back to the demo */
    choose('kpg193');
    await wait(2500);
    check('demo after the analyses: Artifact values, no analysis left', state().source === 'demo' && JSON.stringify(kpis()) === JSON.stringify(DEMO_KPG)
      && byId('kemsSide').children.length === 0 && window.kemsConsole.map.counts() === null, kpis());
  }

  // The abort signal of every analysis request the console sends (the requests themselves still go to the app).
  function trackAnalysisRequests() {
    const sent = [];
    const realFetch = window.fetch;
    window.fetch = (url, options = {}) => {
      if (/security-analysis|sensitivity-analysis/.test(String(url)) && !String(url).includes('__probe=')) sent.push({url: String(url), signal: options.signal});
      return realFetch(url, options);
    };
    const last = (fragment) => [...sent].reverse().find((item) => item.url.includes(fragment));
    return {
      aborted: (fragment) => { const item = last(fragment); return !!item && !!item.signal && item.signal.aborted; },  // the latest such request
      urls: () => sent.map((item) => item.url),
      restore: () => { window.fetch = realFetch; },
    };
  }

  async function analysisStaleScenario() {
    await window.kemsConsole.ready;
    countEngineCalls();
    const tracked = trackAnalysisRequests();
    const aborted = tracked.aborted;
    await chooseApi('kr-kpg193');
    engine.calls = 0;
    // An N-1 answer that arrives after the switch to Mongolia.
    const kpg = await gate('/api/security-analysis?outage_line_id=LINE-16-28');
    check('select LINE-16-28', selectLine('LINE-16-28'));
    cardRun().click();
    await requested(kpg);
    check('pending N-1: loading shown, no result or overlay, button locked', an().security.status === 'loading' && n1Sec().textContent.includes('LINE-16-28 사고 분석 중')
      && cardText().includes('LINE-16-28 사고 분석 중') && cardRun().disabled && overlay() === null && sensRun().disabled);
    await chooseApi('mn-osm-35k');
    check('grid switch aborted the pending KPG N-1 request', aborted('/api/security-analysis?outage_line_id=LINE-16-28'), tracked.urls());
    await release(kpg);
    for (let i = 0; i < 30; i++) await tick();
    check('late KPG N-1 dropped after switching to Mongolia', view().gridId === 'mn-osm-35k' && an().security.status === 'idle' && overlay() === null
      && !byId('kemsSide').textContent.includes('LINE-16-28'), an());

    // Two outages: the earlier one answers after the later one was run.
    const first = '751261406-1_0', second = '222087807-1_0';
    const held = await gate(`/api/grids/mn-osm-35k/security-analysis?outage_line_id=${first}`);
    check(`select ${first}`, selectLine(first));
    cardRun().click();
    await requested(held);
    await runN1(second);
    check('running another outage aborted the earlier N-1 request', aborted(`security-analysis?outage_line_id=${first}`) && !aborted(`security-analysis?outage_line_id=${second}`));
    await release(held);
    for (let i = 0; i < 30; i++) await tick();
    check('late N-1 of the earlier outage dropped', an().line === second && an().security.status === 'ready' && overlay().outage[0] === second
      && fact(n1Sec(), '사고 선로').startsWith(second) && byId('kemsCard').dataset.id === second, an());

    // A Sensitivity answer that arrives after a new N-1.
    await runN1(first);
    const sens = await gate(`/api/grids/mn-osm-35k/sensitivity-analysis?outage_line_id=${first}`);
    sensRun().click();
    await requested(sens);
    check('pending Sensitivity: loading shown, N-1 result kept', an().sensitivity.status === 'loading' && sensSec().textContent.includes('Sensitivity 계산 중')
      && an().security.status === 'ready' && sensRun().disabled);
    await runN1(second);
    check('a new N-1 aborted the pending Sensitivity request', aborted(`sensitivity-analysis?outage_line_id=${first}`));
    await release(sens);
    for (let i = 0; i < 30; i++) await tick();
    check('late Sensitivity dropped after the outage changed', an().line === second && an().sensitivity.status === 'idle' && overlay().candidates.length === 0
      && overlay().monitored.length === 0 && !sensSec().querySelector('[data-list="candidates"]'), an().sensitivity);

    // Re-running the same outage drops the Sensitivity of the previous run.
    await runN1(first);
    await runSensitivity('');
    check('Sensitivity shown for the first outage', an().sensitivity.state === 'completed');
    const again = await gate(`/api/grids/mn-osm-35k/security-analysis?outage_line_id=${first}`);
    cardRun().click();
    await requested(again);
    check('re-run: earlier Sensitivity removed while the new N-1 runs', an().security.status === 'loading' && an().sensitivity.status === 'idle'
      && overlay() === null && !sensSec().querySelector('[data-list="candidates"]'));
    await release(again);
    await until(() => an().security.status === 'ready', 're-run ready');
    check('DC engine idle while analysis responses were pending', engine.calls === 0, engine.calls);

    // A failing N-1 held until after the return to a demo grid (whose own DC engine then runs).
    const late = await gate(`/api/grids/mn-osm-35k/security-analysis?outage_line_id=${second}`, true);
    check(`select ${second}`, selectLine(second));
    cardRun().click();
    await requested(late);
    choose('six');  // no timers while a response is held: Chrome virtual time pauses on pending fetches
    await until(() => state().source === 'demo' && text('kLoad') === '750 MW', 'six loaded');
    await release(late);
    for (let i = 0; i < 30; i++) await tick();
    check('late failed N-1 after leaving the API grid changes nothing', state().source === 'demo' && byId('kemsSide').children.length === 0
      && window.kemsConsole.map.counts() === null && text('kLoad') === '750 MW', kpis());
    check('leaving for the demo aborted the pending N-1 request', aborted(`security-analysis?outage_line_id=${second}`));
    tracked.restore();
  }

  /* A changed monitored line voids the Sensitivity shown or pending; the N-1 result stays. Held answers
   * (test proxy gates) for an earlier choice are released afterwards and must never reach the current one. */
  async function analysisMonitoredScenario() {
    await window.kemsConsole.ready;
    countEngineCalls();
    const tracked = trackAnalysisRequests();
    const pick = (value) => { monitorSelect().value = value; monitorSelect().dispatchEvent(new Event('change')); };
    const drain = async () => { for (let i = 0; i < 30; i++) await tick(); };
    // Starts a run whose answer the proxy holds and returns its gate, or null after a failed check when no
    // request for `fragment` went out. A 'loading' state is no proof (an aborted run can leave one behind),
    // and waiting on an unclaimed gate would only end at the proxy's timeout: virtual time is paused meanwhile.
    const gates = [];
    const runHeld = async (fragment, label, fail = false) => {
      const id = await gate(fragment, fail);
      gates.push(id);
      const before = tracked.urls().length;
      sensRun().click();
      const sent = tracked.urls().slice(before).some((url) => url.includes(fragment));
      check(`${label}: request sent and held`, sent, tracked.urls().slice(before));
      if (!sent) return null;
      await requested(id);
      return id;
    };
    const n1Part = (drawn) => JSON.stringify(drawn && {...drawn, monitored: undefined, candidates: undefined});
    const noSensitivity = () => an().sensitivity.status === 'idle' && sensSec().dataset.status === 'idle' && !sensSec().querySelector('[data-list="candidates"]')
      && overlay().monitored.length === 0 && overlay().candidates.length === 0 && !svg().querySelector('.k-an-mon') && !svg().querySelector('.k-an-cand');

    try {
      /* KPG-193 LINE-80-82: violated lines and voltage-level buses stay highlighted while the monitored line changes */
      await chooseApi('kr-kpg193');
      engine.calls = 0;
      const outage = 'LINE-80-82';
      await runN1(outage);
      const n1Overlay = overlay();
      const n1Text = n1Sec().textContent;
      const keptN1 = (label) => check(`${label}: N-1 result and its outage/violation highlights unchanged`, an().security.status === 'ready' && an().line === outage
        && n1Sec().textContent === n1Text && n1Part(overlay()) === n1Part(n1Overlay) && view().mode === 'analysis'
        && visible(svg().querySelector('.k-an-outage')) && visible(svg().querySelector('.k-an-viol')) && visible(svg().querySelector('.k-an-violdot')), overlay());
      const lines = [...monitorSelect().options].map((o) => o.value).filter(Boolean);
      const [first, second, third] = lines;
      const ref = {};
      for (const line of [first, second, third]) {
        ref[line] = (await apiCall('/api/sensitivity-analysis', {outage_line_id: outage, monitored_line_id: line, top_n: '5', grid_id: 'kr-kpg193'})).body;
      }
      check('KPG: three monitored lines with distinct rankings, so a mixed-up answer is visible', lines.length >= 3
        && new Set([first, second, third].map((line) => JSON.stringify(ref[line].candidates.map((c) => [c.generator_id, c.sensitivity])))).size === 3, lines);

      // 1. A result on screen, then another line chosen: cleared at once, nothing requested by the change.
      await runSensitivity(first);
      check('result for the first monitored line', an().sensitivity.state === 'completed' && an().sensitivity.monitored === first && overlay().monitored[0] === first
        && overlay().candidates.length > 0 && visible(svg().querySelector('.k-an-cand')));
      candidatesMatch('first monitored line', ref[first].candidates);
      sensSec().querySelector('.kems-an-bus').click();
      const candidateBus = byId('kemsCard').dataset.id;
      check('a candidate bus card lists the result', byId('kemsCard').dataset.kind === 'bus' && (cardValue('민감도 후보') || '').startsWith(`1. ${ref[first].candidates[0].generator_id}`));
      byId('zfit').click();
      let requests = (await analysisHits()).length;
      pick(second);
      check('change after a result: Sensitivity result cleared at once', noSensitivity() && an().monitored === second && an().sensitivity.requested === null
        && !sensRun().disabled && sensRun().textContent === 'Sensitivity 실행', {analysis: an(), drawn: overlay()});
      check('change after a result: legend no longer shows the monitored line or candidates', !svg().querySelector('.k-an-rank')
        && !byId('kemsLegend').textContent.includes('감시 선로 (Sensitivity)') && !byId('kemsLegend').textContent.includes('민감도 후보 모선'), byId('kemsLegend').textContent);
      check('change after a result: the open candidate card drops the cleared result', byId('kemsCard').dataset.id === candidateBus && cardValue('민감도 후보') === null, cardText().slice(-160));
      keptN1('change after a result');
      check('change after a result: the select keeps the choice and the keyboard focus, nothing is requested', monitorSelect().value === second
        && document.activeElement === monitorSelect() && (await analysisHits()).length === requests);

      // 2. A pending request, then another line chosen: aborted at once, its late answer never shown.
      const held = await runHeld(`monitored_line_id=${second}&`, 'second line');
      if (held === null) return;
      check('pending for the second line: loading shown', an().sensitivity.status === 'loading' && an().sensitivity.requested === second
        && sensSec().textContent.includes(`Sensitivity 계산 중… (감시 선로 ${second})`) && sensRun().disabled);
      pick(third);
      check('change while pending: request aborted and loading cleared', tracked.aborted(`monitored_line_id=${second}&`) && noSensitivity() && an().monitored === third
        && !sensSec().textContent.includes('Sensitivity 계산 중') && !sensRun().disabled, an().sensitivity);
      await release(held);
      await drain();
      check('late answer for the abandoned line never shown', noSensitivity() && an().monitored === third && an().sensitivity.error === null, {analysis: an(), drawn: overlay()});
      keptN1('late answer after a change');

      // 3. Older request held, newer line answered first, older answer released afterwards.
      const older = await runHeld(`monitored_line_id=${third}&`, 'older third line');
      if (older === null) return;
      pick(first);
      await runSensitivity();
      check('newer choice answered for itself', an().sensitivity.state === 'completed' && an().sensitivity.requested === first && an().sensitivity.monitored === first
        && tracked.aborted(`monitored_line_id=${third}&`));
      await release(older);
      await drain();
      check('older answer released after the newer result: the newer result stays', an().sensitivity.monitored === first && overlay().monitored[0] === first
        && JSON.stringify(an().sensitivity.candidates.map((c) => c.generator)) === JSON.stringify(ref[first].candidates.map((c) => c.generator_id)), an().sensitivity);
      candidatesMatch('newer result after the older answer', ref[first].candidates);
      keptN1('older answer after a newer result');

      // 4. Two pending requests: the older answer arrives while the newer one is still pending.
      pick(second);
      const g2 = await runHeld(`monitored_line_id=${second}&`, 'two pending, second line');
      if (g2 === null) return;
      pick(third);
      const g3 = await runHeld(`monitored_line_id=${third}&`, 'two pending, third line');
      if (g3 === null) return;
      await release(g2);
      await drain();
      check('older answer while the newer is pending: ignored, the newer line still loading', an().sensitivity.status === 'loading' && an().sensitivity.requested === third
        && !sensSec().querySelector('[data-list="candidates"]') && overlay().candidates.length === 0 && tracked.aborted(`monitored_line_id=${second}&`)
        && !tracked.aborted(`monitored_line_id=${third}&`), an().sensitivity);
      await release(g3);
      await until(() => an().sensitivity.status === 'ready', 'newer answer');
      check('then the newer answer is shown for its own line', an().sensitivity.monitored === third && overlay().monitored[0] === third);
      candidatesMatch('newer answer', ref[third].candidates);
      keptN1('two pending answers');
      pick(third);
      check('re-selecting the same monitored line is not a change: its result stays', an().sensitivity.state === 'completed' && an().sensitivity.monitored === third
        && overlay().monitored[0] === third && !!sensSec().querySelector('[data-list="candidates"]'));

      // 5. A failing answer held for an abandoned line shows no error on the current choice.
      pick(first);
      const failing = await runHeld(`monitored_line_id=${first}&`, 'failing first line', true);
      if (failing === null) return;
      pick(second);
      await release(failing);
      await drain();
      check('late failure for the abandoned line shows no error', noSensitivity() && an().sensitivity.error === null && an().monitored === second
        && !sensSec().textContent.includes('Sensitivity 실패'), an().sensitivity);

      // 6. EMS API selection, then that same line chosen explicitly: the request differs, so the result is cleared.
      pick('');
      await runSensitivity();
      const autoLine = an().sensitivity.monitored;
      check('EMS API selection answered', an().sensitivity.state === 'completed' && an().sensitivity.requested === null && lines.includes(autoLine), autoLine);
      pick(autoLine);
      check('explicit choice of the line the API selected clears the auto result', noSensitivity() && an().monitored === autoLine, an().sensitivity);
      keptN1('auto to explicit');

      /* Mongolia: an API-selected run pending, then another line chosen */
      await chooseApi('mn-osm-35k');
      const mesh = '751261406-1_0';
      await runN1(mesh);
      const mnOverlay = overlay();
      const mnLine = [...monitorSelect().options].map((o) => o.value).filter(Boolean)[1];
      const mnRef = (await apiCall('/api/grids/mn-osm-35k/sensitivity-analysis', {outage_line_id: mesh, monitored_line_id: mnLine, top_n: '5'})).body;
      const auto = await runHeld(`sensitivity-analysis?outage_line_id=${mesh}&top_n=5`, '몽골 API-selected run');
      if (auto === null) return;
      pick(mnLine);
      check('몽골: change while the API-selected run is pending aborts it', tracked.aborted(`sensitivity-analysis?outage_line_id=${mesh}&top_n=5`) && noSensitivity()
        && an().monitored === mnLine);
      await release(auto);
      await drain();
      check('몽골: the late API-selected answer is not shown', noSensitivity() && an().sensitivity.error === null);
      await runSensitivity();
      check('몽골: the chosen line answered for itself', an().sensitivity.monitored === mnLine && an().sensitivity.requested === mnLine
        && fact(sensSec(), '선정') === '사용자 선택' && overlay().monitored[0] === mnLine);
      candidatesMatch('몽골 chosen line', mnRef.candidates);
      check('몽골: outage highlight and N-1 result kept throughout', an().security.status === 'ready' && an().line === mesh && n1Part(overlay()) === n1Part(mnOverlay));
      check('DC engine idle through every change', engine.calls === 0, engine.calls);
    } finally {
      for (const id of gates) await release(id);  // a held answer never outlives the scenario
      tracked.restore();
    }
  }

  async function analysisErrorScenario() {
    await window.kemsConsole.ready;
    countEngineCalls();
    const line = '751261406-1_0';
    await chooseApi('mn-osm-35k');
    engine.calls = 0;
    const noDemo = () => DEMO_FIGURES.every((figure) => !document.body.innerText.includes(figure)) && demoHidden();
    for (const status of [404, 422, 500, 503]) {
      await rule({match: '/api/grids/mn-osm-35k/security-analysis', status, detail: `test proxy ${status}`, once: true});
      await runN1(line);
      check(`N-1 HTTP ${status}: error shown, no result, no demo values`, an().security.status === 'error' && an().security.error === `HTTP ${status} · test proxy ${status}`
        && n1Sec().textContent.includes(`HTTP ${status} · test proxy ${status} · 데모 결과로 대체하지 않습니다`) && cardText().includes(`N-1 실패 · HTTP ${status}`)
        && overlay() === null && view().mode !== 'analysis' && sensRun().disabled && noDemo(), an().security);
    }
    await chooseApi('kr-kpg193');
    await rule({match: '/api/security-analysis', status: 500, text: 'Internal Server Error', once: true});
    await runN1('LINE-16-28');
    check('N-1 HTTP 500 with a non-JSON body (as FastAPI answers an unhandled error)', an().security.status === 'error' && an().security.error === 'HTTP 500', an().security);
    await rule({match: '/api/security-analysis', drop: true, once: true});
    await runN1('LINE-16-28');
    check('N-1 network error', an().security.status === 'error' && an().security.error === 'EMS API 서버에 연결할 수 없습니다' && overlay() === null, an().security);

    await runN1('LINE-16-28');
    check('real N-1 after the failures', an().security.status === 'ready' && an().security.verdict === 'violations');
    for (const status of [404, 422, 500, 503]) {
      await rule({match: '/api/sensitivity-analysis', status, detail: `test proxy ${status}`, once: true});
      await runSensitivity('');
      check(`Sensitivity HTTP ${status}: error shown, N-1 result kept, no ranking`, an().sensitivity.status === 'error' && an().sensitivity.error === `HTTP ${status} · test proxy ${status}`
        && an().security.status === 'ready' && sensSec().textContent.includes('데모 결과로 대체하지 않습니다') && !sensSec().querySelector('[data-list="candidates"]')
        && overlay().candidates.length === 0, an().sensitivity);
    }
    await rule({match: '/api/sensitivity-analysis', drop: true, once: true});
    await runSensitivity('');
    check('Sensitivity network error', an().sensitivity.error === 'EMS API 서버에 연결할 수 없습니다');

    // Responses that do not belong to the request (fields overwritten by the test proxy).
    const invalid = async (label, match, merge, run, expected) => {
      await rule({match, merge, once: true});
      await run();
      const status = match.includes('sensitivity') ? an().sensitivity : an().security;
      const section = match.includes('sensitivity') ? sensSec() : n1Sec();
      check(`${label}: rejected, nothing shown from it`, status.status === 'invalid' && status.error.includes(expected) && section.textContent.includes('결과를 표시하지 않습니다')
        && (match.includes('sensitivity') ? !section.querySelector('[data-list="candidates"]') : overlay() === null), status);
    };
    await invalid('N-1 answer for another outage line', '/api/security-analysis', {outage_line_id: 'LINE-1-2'}, () => runN1('LINE-16-28'), '요청한 선로(LINE-16-28)와 다릅니다');
    await invalid('N-1 assessed without a converged state', '/api/security-analysis', {post_status: 'MAX_ITERATION_REACHED'}, () => runN1('LINE-16-28'), '위반 판정 가능 여부가 맞지 않습니다');
    await invalid('N-1 count without the list', '/api/security-analysis', {violated_equipment_count: 0}, () => runN1('LINE-16-28'), '위반 설비 수(0)와 위반 목록(1건)이 맞지 않습니다');
    await runN1('LINE-16-28');
    await invalid('Sensitivity candidate_count mismatch', '/api/sensitivity-analysis', {candidate_count: 9}, () => runSensitivity(''), 'candidate_count(9)와 후보 목록(5건)이 맞지 않습니다');
    await invalid('Sensitivity with candidates but not completed', '/api/sensitivity-analysis', {analysis_status: 'NO_OVERLOAD'}, () => runSensitivity(''), 'NO_OVERLOAD 응답에 후보가 있습니다');

    await chooseApi('mn-osm-35k');
    await invalid('몽골 N-1 answer of another grid', '/api/grids/mn-osm-35k/security-analysis', {grid_id: 'kr-kpg193'}, () => runN1(line), '응답의 계통 ID(kr-kpg193)');
    await invalid('몽골 N-1 outage ends unlike the map', '/api/grids/mn-osm-35k/security-analysis', {outage_line: {bus0: '1', bus1: '2', voltage_kv: 110}}, () => runN1(line), '지도');
    // 모의 응답: the backend's non-converged shape (no real Mongolia contingency diverges).
    await rule({match: '/api/grids/mn-osm-35k/security-analysis', once: true, merge: {post_status: 'MAX_ITERATION_REACHED', analysis_status: 'SECURITY_NOT_CONVERGED',
      violation_assessment_available: false, main_component_converged: false, violated_equipment_count: null, violations: [], post_voltage_min_pu: null,
      post_voltage_max_pu: null, max_voltage_change_pu: null, max_line_p_change_mw: null, max_line_q_change_mvar: null, post_max_line_loading_percent: null,
      top_voltage_changes: null, top_line_flow_changes: null, generator_q_screening: null}});
    await runN1(line);
    check('몽골 N-1 non-converged (모의 응답): 평가 불가, no post-contingency values', an().security.verdict === 'not_assessed' && fact(n1Sec(), '위반 설비 수').startsWith('평가 불가')
      && fact(n1Sec(), '사고 후 최대 선로 부하율') === null && !n1Sec().querySelector('[data-list="flows"]') && pills(n1Sec())[0] === '평가 불가 · 사고 후 PF MAX_ITERATION_REACHED'
      && overlay().violationLines.length === 0, an().security);
    // 모의 응답: violations on a real line and a real bus of the Mongolia map (no real contingency violates).
    const violations = [
      {equipment_id: '723574237-1_0', equipment_type: 'Line', iidm_id: 'L_723574237-1_0', limit_type: 'APPARENT_POWER', limit_name: 'permanent', unit: 'MVA',
        sides: ['ONE'], record_count: 1, limit: 122.9, value: 130.1, violation_amount: 7.2, violation_direction: 'above_maximum', loading_percent: 105.858},
      {equipment_id: '3', equipment_type: 'Bus', iidm_id: 'VL_3', limit_type: 'LOW_VOLTAGE', limit_name: '', unit: 'kV', sides: [], record_count: 1,
        limit: 99, value: 97.46, violation_amount: 1.54, violation_direction: 'below_minimum'}];
    await rule({match: '/api/grids/mn-osm-35k/security-analysis', once: true, merge: {violated_equipment_count: 2, violations}});
    await runN1(line);
    violationsMatch('몽골 N-1 violations (모의 응답)', violations, mnTarget);
    check('몽골 N-1 violations (모의 응답): violated line and bus highlighted', JSON.stringify(overlay().violationLines) === '["723574237-1_0"]'
      && JSON.stringify(overlay().violationBuses) === '["3"]' && pills(n1Sec())[0] === '위반 2건' && n1Sec().textContent.includes('하한 99.00 kV · 계산 97.46 kV'), overlay());

    // Catalog flags off (모의 카탈로그): no request is sent, the API note is shown.
    await rule({match: '=/api/grids', grid: 'mn-osm-35k', merge: {security_analysis_available: false, security_analysis_note: '모의: 몽골 모델 검증 실패',
      sensitivity_analysis_available: false, sensitivity_analysis_note: '모의: 몽골 모델 검증 실패'}});
    await chooseApi('mn-osm-35k');
    let sent = (await analysisHits()).length;
    check('security flag false: N-1 disabled with the API note', selectLine(line) && cardRun().disabled && cardText().includes('모의: 몽골 모델 검증 실패')
      && n1Sec().dataset.status === 'unsupported' && sensSec().dataset.status === 'unsupported', cardText().slice(-160));
    cardRun().click();
    for (let i = 0; i < 20; i++) await tick();
    check('security flag false: no request, not sent to another grid', (await analysisHits()).length === sent && an().security.status === 'idle');
    await clearRules();
    await rule({match: '=/api/grids', grid: 'mn-osm-35k', merge: {sensitivity_analysis_available: false, sensitivity_analysis_note: '모의: 민감도 자체 검증 실패'}});
    await chooseApi('mn-osm-35k');
    await runN1(line);
    sent = (await analysisHits()).length;
    check('sensitivity flag false: N-1 runs, Sensitivity disabled with the note', an().security.status === 'ready' && sensSec().dataset.status === 'unsupported'
      && sensSec().textContent.includes('모의: 민감도 자체 검증 실패') && !sensRun() && byId('ctxInfo').textContent.includes('N-1 연동'));
    for (let i = 0; i < 20; i++) await tick();
    check('sensitivity flag false: no Sensitivity request', (await analysisHits()).length === sent);
    await clearRules();

    // Without the PF preview the console cannot know a line is excluded: the real EMS API refuses it (real HTTP 422).
    const excluded = String((await getJson('/api/grids/mn-osm-35k/pf-preview')).metadata.excluded_line_ids[0]);
    await rule({match: '=/api/grids/mn-osm-35k/pf-preview', status: 500, once: true});
    await chooseApi('mn-osm-35k');
    await runN1(excluded);
    check('real HTTP 422 from the EMS API for a PF-excluded line', view().pf.status === 'error' && an().security.status === 'error'
      && an().security.error.startsWith('HTTP 422 · ') && an().security.error.includes('제외된 선로'), an().security);

    // An N-1 that never answers times out.
    await chooseApi('mn-osm-35k');
    const realFetch = window.fetch;
    window.fetch = (url, options = {}) => (String(url).includes('/security-analysis')
      ? new Promise((resolve, reject) => options.signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError'))))
      : realFetch(url, options));
    check(`select ${line}`, selectLine(line));
    cardRun().click();
    await wait(61000);
    window.fetch = realFetch;
    check('N-1 timeout', an().security.status === 'error' && an().security.error === '응답 시간 초과 (60초)' && overlay() === null, an().security);
    check('DC engine idle through every failure', engine.calls === 0, engine.calls);
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
      'api-gate': 'kr-kpg193', 'api-error': 'kr-kpg193', 'api-pf-error': 'kr-kpg193', 'api-gis-fallback': 'mn-osm-35k',
      'api-kpg-n1': 'kr-kpg193', 'api-kpg-n1-multi': 'kr-kpg193', 'api-kpg-sens': 'kr-kpg193', 'api-kpg-n1-nonconv': 'kr-kpg193',
      'api-mn-n1-island': 'mn-osm-35k', 'api-mn-sens': 'mn-osm-35k', 'api-kr-analysis': 'kr-osm-transmission', 'api-n1-error': 'mn-osm-35k'}[name];
    if (grid) await chooseApi(grid);
    const sideTo = (section) => { const side = byId('kemsSide'); side.scrollTop += section.getBoundingClientRect().top - side.getBoundingClientRect().top - 4; };
    if (name === 'api-kpg-n1') await runN1('LINE-16-28');
    if (name === 'api-kpg-n1-multi') await runN1('LINE-80-82');
    if (name === 'api-kpg-n1-nonconv') await runN1('LINE-28-80');
    if (name === 'api-mn-n1-island') await runN1('222087807-1_0');
    if (name === 'api-kpg-sens' || name === 'api-mn-sens') {
      await runN1(name === 'api-kpg-sens' ? 'LINE-16-28' : '751261406-1_0');
      await runSensitivity('');
      const bus = sensSec().querySelector('.kems-an-bus');
      if (bus) bus.click();  // the first candidate's bus card, then the whole grid again
      byId('zfit').click();
      sideTo(sensSec());
    }
    if (name === 'api-kr-analysis') selectLine(String((await getJson('/api/grids/kr-osm-transmission/map')).lines[0].line_id));
    if (name === 'api-n1-error') {
      await rule({match: '/api/grids/mn-osm-35k/security-analysis', status: 503, detail: '몽골 Security Analysis를 사용할 수 없습니다: test proxy 503'});
      await runN1('751261406-1_0');
    }
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
    if (mode === 'analysis') await analysisScenario();
    if (mode === 'analysis-stale') await analysisStaleScenario();
    if (mode === 'analysis-monitored') await analysisMonitoredScenario();
    if (mode === 'analysis-errors') await analysisErrorScenario();
    if (mode === 'fonts') await fontsScenario();
    if (mode === 'shot') await shotScenario(params.get('shot'));
  } catch (error) {
    check('probe exception', false, String((error && error.stack) || error));
  }
  finish();
  if (mode === 'perf') await fetch('/__probe__/release-load', {method: 'POST'});
})();
