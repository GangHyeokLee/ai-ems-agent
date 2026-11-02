/* Browser probe for the Artifact console (/console/) and, in parity mode, the original Artifact.
 * Injected by tests/test_console_browser.py after the page scripts; the report is written to a
 * hidden pre element (id __probe_result). Modes (?probe=):
 *   parity   demo fingerprint (same steps on the original Artifact and on /console/)
 *   api      EMS API grid selection, demo isolation, return to demo
 *   fail500 | drop | badjson   initial GET /api/grids failure (forced by the test proxy)
 *   stale    held /api/grids responses released out of order (test proxy gates)
 *   timeout  GET /api/grids that never answers (fetch stand-in honouring the abort signal)
 *   fonts    Google Fonts unreachable (Chrome resolver rule)
 *   shot     end state for a screenshot (&shot=<state>)
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
  const tick = () => fetch('/__probe__/tick', {cache: 'no-store'});
  const norm = (value) => String(value == null ? '' : value).replace(/\s+/g, ' ')
    .replace(/\d+(\.\d+)?s\b/g, '#s').replace(/\d{1,2}:\d{2}(:\d{2})?/g, 'HH:MM').replace(/\d+\s*(초|분) 전/g, '# 전').trim();
  const kpis = () => ({load: text('kLoad'), gen: text('kGen'), max: text('kMax'), n1: text('kN1')});
  const finish = (extra = {}) => {
    const pre = document.createElement('pre');
    pre.id = '__probe_result';
    pre.hidden = true;
    pre.textContent = JSON.stringify({mode, results, errors: window.__probeErrors || [], ...extra});
    document.body.appendChild(pre);
  };
  async function until(predicate, label, limit = 600) {
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
      fp.views[view] = {on: section.classList.contains('on'), length: body.length, text: body, svg: section.querySelectorAll('svg').length};
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

  /* ---------- EMS API grid helpers (console only) ---------- */
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
  async function chooseApi(id) {
    choose(`api:${id}`);
    return until(() => { const s = state(); return s.gridId === id && s.panel && s.panel.status !== 'loading'; }, `panel ${id}`);
  }
  const apiOptions = () => [...byId('model').querySelectorAll('optgroup')[0].children].map((o) => ({value: o.value, label: o.textContent, disabled: o.disabled}));
  const capabilityCell = (flag) => {
    const row = document.querySelector(`#kemsApi tr[data-flag="${flag}"]`);
    return row ? row.children[1].textContent : null;
  };
  const expectedCell = (entry, flag) => (entry[flag] === true ? '제공' : entry[flag] === false ? '미제공' : '응답에 없음');
  const DEMO_FIGURES = ['55,606', '6,254', '20건'];

  async function apiPanelMatches(entry, label) {
    const panel = byId('kemsApi');
    check(`${label}: API panel visible, demo ops hidden`, visible(panel) && !visible(document.querySelector('#v-ops > .grid')) && !visible(byId('kLoad')) && !visible(byId('alarms')));
    check(`${label}: panel title is the API name`, panel.querySelector('h2').textContent === entry.name, panel.querySelector('h2').textContent);
    check(`${label}: grid_id from API`, panel.querySelector('.sub code').textContent === entry.id);
    const flags = Object.keys(entry).filter((key) => key.endsWith('_available'));
    const cells = Object.fromEntries(flags.map((flag) => [flag, capabilityCell(flag)]));
    check(`${label}: every capability shown exactly as /api/grids`, flags.length > 0 && flags.every((flag) => cells[flag] === expectedCell(entry, flag)), cells);
    const consoleCells = [...panel.querySelectorAll('tr[data-flag]')].map((row) => row.children[2].textContent);
    check(`${label}: nothing claimed as wired in this console`, consoleCells.every((value) => value === '미연동' || value === '—'), consoleCells);
    check(`${label}: KPI tiles carry no numbers`, [...panel.querySelectorAll('.kems-kpis .kpi b')].every((b) => b.textContent === '—'));
    const shown = document.body.innerText;
    check(`${label}: no demo figure visible`, DEMO_FIGURES.every((figure) => !shown.includes(figure)), DEMO_FIGURES.filter((f) => shown.includes(f)));
    check(`${label}: header marks EMS API`, text('twMode') === 'EMS API' && byId('ctxInfo').textContent.includes('EMS API 계통') && byId('ctxInfo').textContent.includes(entry.id));
    check(`${label}: select shows the API grid`, byId('model').value === `api:${entry.id}`);
    check(`${label}: panel renders text only`, !panel.textContent.includes('[object') && !panel.textContent.includes('undefined'));
    const facts = [...panel.querySelectorAll('.kems-facts dt')].map((dt) => [dt.textContent, dt.nextElementSibling.textContent]);
    check(`${label}: API facts listed`, facts.length > 0 && facts.every(([, value]) => Object.values(entry).includes(value)), facts);
    check(`${label}: no retry offered after a successful check`, !visible(document.querySelector('.kems-retry')));
    if (typeof entry.note === 'string') check(`${label}: API note shown verbatim`, panel.textContent.includes(entry.note));
  }

  async function apiScenario() {
    await window.kemsConsole.ready;
    const catalog = await (await fetch('/api/grids', {cache: 'no-store'})).json();
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
    check('demo figures on load', JSON.stringify(kpis()) === JSON.stringify({load: '55,606 MW', gen: '55,606 MW', max: '98%', n1: '20건'}), kpis());
    check('demo header badge', byId('ctxInfo').querySelector('.kems-src-demo') !== null && byId('ctxInfo').textContent.includes('데모 계통'));
    check('live toggle labelled as simulation', document.querySelector('.mtog button[data-m="live"]').textContent === '실계통 연동 (모의)');
    check('API panel empty in demo', byId('kemsApi').children.length === 0 && !visible(byId('kemsApi')));
    check('demo screens not gated in demo', [...document.querySelectorAll('.kems-gate')].every((g) => !visible(g)));

    countEngineCalls();
    const messages = byId('msgs').children.length;
    await chooseApi('kr-kpg193');
    engine.calls = 0;
    check('KPG-193 API selected', state().source === 'api' && state().gridId === 'kr-kpg193' && state().panel.status === 'ready', state());
    await apiPanelMatches(entry('kr-kpg193'), 'KPG-193');
    check('KPG-193: mode toggle and shared analyses disabled', [...document.querySelectorAll('.mtog button')].every((b) => b.disabled) && byId('apBtn').disabled);
    check('KPG-193: chat hidden', !visible(byId('q')) && !visible(byId('msgs')));
    await ask('현재 계통 상태 알려줘');
    await switchMode('live');
    check('KPG-193: demo chat and live switch inert', byId('msgs').children.length === messages && MODE === 'pub');
    for (const view of VIEWS.filter((v) => v !== 'ops')) {
      nav(view);
      await wait(400);
      const section = byId(`v-${view}`);
      const gate = section.querySelector('.kems-gate');
      const others = [...section.children].filter((child) => !child.classList.contains('vhead') && child !== gate);
      check(`KPG-193: ${view} demo screen gated`, visible(gate) && others.every((child) => !visible(child)), others.filter(visible).map((c) => c.className));
    }
    nav('ops');
    dispatchEvent(new Event('resize'));
    await wait(8000);
    check('KPG-193: DC engine idle in API mode', engine.calls === 0, engine.calls);

    await chooseApi('mn-osm-35k');
    await apiPanelMatches(entry('mn-osm-35k'), '몽골 OSM');
    check('몽골 OSM: no KPG text left in panel', !byId('kemsApi').textContent.includes(entry('kr-kpg193').name));

    await chooseApi('kr-osm-transmission');
    await apiPanelMatches(entry('kr-osm-transmission'), '한국 OSM');
    check('한국 OSM: chat reported unsupported by API', byId('kemsApi').textContent.includes('AI 채팅을 지원하지 않습니다'));
    check('한국 OSM: no 몽골 text left in panel', !byId('kemsApi').textContent.includes(entry('mn-osm-35k').name));
    check('API grids never ran the DC engine', engine.calls === 0, engine.calls);

    nav('db');
    await wait(300);
    document.querySelector('#v-db .kems-gate button').click();
    await wait(2500);
    check('gate button returns to the last demo grid', state().source === 'demo' && byId('model').value === 'kpg193'
      && JSON.stringify(kpis()) === JSON.stringify({load: '55,606 MW', gen: '55,606 MW', max: '98%', n1: '20건'}), kpis());
    check('dashboard re-rendered for the demo grid', visible(byId('dKpi')) && byId('dKpi').textContent.includes('55,606')
      && !visible(document.querySelector('#v-db .kems-gate')), byId('dKpi').textContent.slice(0, 120));
    nav('ops');
    await wait(300);
    check('API panel cleared after leaving', byId('kemsApi').children.length === 0 && document.body.dataset.kemsSrc === 'demo');
    check('mode toggle enabled again', [...document.querySelectorAll('.mtog button')].every((b) => !b.disabled) && !byId('apBtn').disabled);

    await chooseApi('kr-kpg193');
    choose('ieee39');
    await wait(2500);
    check('API -> IEEE39 demo loads IEEE39 values', byId('model').value === 'ieee39' && text('kLoad') === '6,254 MW' && text('kN1') === '20건', kpis());
    check('header follows the demo grid', byId('ctxInfo').textContent.includes('IEEE 39'), byId('ctxInfo').textContent);
    choose('kpg193');
    await wait(2500);
    const before = byId('msgs').children.length;
    await ask('현재 계통 상태 알려줘');
    await settle();
    check('demo chat works after returning', byId('msgs').children.length === before + 2);
    check('demo engine runs again in demo mode', engine.calls > 0, engine.calls);

    document.querySelector('.mtog button[data-m="live"]').click();
    await wait(4000);
    const ctx = byId('ctxInfo');
    check('simulated live mode labelled', MODE === 'live' && visible(ctx.querySelector('.kems-src-sim')) && ctx.textContent.includes('실계통 연동 (모의)'), ctx.textContent);
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

  /* ---------- failures ---------- */
  async function initialFailure(expected) {
    await window.kemsConsole.ready;
    const options = apiOptions();
    check('no API grid offered after a failed catalog', options.length === 1 && options[0].disabled && options[0].value === '', options);
    check(`failure reason shown (${expected})`, options[0].label.includes(expected), options[0].label);
    check('retry button visible', visible(document.querySelector('.kems-retry')));
    check('stays on the demo, no fallback', state().source === 'demo' && state().catalogError && byId('model').value === 'kpg193');
    check('demo unaffected', JSON.stringify(kpis()) === JSON.stringify({load: '55,606 MW', gen: '55,606 MW', max: '98%', n1: '20건'}), kpis());
    document.querySelector('.kems-retry').click();
    await until(() => state().catalogError !== null && apiOptions()[0].label.includes(expected), 'retry result');
    check('retry reports the same failure', apiOptions()[0].label.includes(expected) && visible(document.querySelector('.kems-retry')));
  }

  async function gate(fail = false) {
    return (await (await fetch('/__probe__/gates', {method: 'POST', body: JSON.stringify({match: '/api/grids', fail})})).json()).id;
  }
  const requested = (id) => fetch(`/__probe__/gates/${id}?wait=1`).then((r) => r.json());
  const release = (id) => fetch(`/__probe__/gates/${id}/release`, {method: 'POST'});
  const panelTitle = () => (byId('kemsApi').querySelector('h2') || {}).textContent;

  async function staleScenario() {
    await window.kemsConsole.ready;
    const catalog = await (await fetch('/api/grids', {cache: 'no-store'})).json();
    const name = (id) => catalog.grids.find((grid) => grid.id === id).name;
    countEngineCalls();

    const first = await gate();
    const second = await gate();
    choose('api:kr-kpg193');
    await requested(first);
    check('pending check shows no capability values', state().panel.status === 'loading' && !byId('kemsApi').querySelector('tr[data-flag]')
      && [...byId('kemsApi').querySelectorAll('.kems-kpis em')].every((em) => em.textContent === '확인 중'));
    choose('api:mn-osm-35k');
    await requested(second);
    await release(second);
    await until(() => state().panel.status === 'ready', 'MN ready');
    check('newer response applied', panelTitle() === name('mn-osm-35k') && capabilityCell('generator_contingency_available') === '미제공');
    await release(first);
    for (let i = 0; i < 20; i++) await tick();
    check('older response dropped', state().gridId === 'mn-osm-35k' && panelTitle() === name('mn-osm-35k') && byId('model').value === 'api:mn-osm-35k', panelTitle());
    check('DC engine idle while API responses were pending', engine.calls === 0, engine.calls);

    const late = await gate();
    choose('api:kr-osm-transmission');
    await requested(late);
    choose('six');  // no timers while a response is held: Chrome virtual time pauses on pending fetches
    await until(() => state().source === 'demo' && text('kLoad') === '750 MW', 'six loaded');
    await release(late);
    for (let i = 0; i < 20; i++) await tick();
    check('late API response after leaving does not reopen API view', state().source === 'demo' && byId('kemsApi').children.length === 0
      && byId('model').value === 'six' && text('kLoad') === '750 MW', kpis());

    const failing = await gate(true);
    choose('api:kr-kpg193');
    engine.calls = 0;  // the six demo above legitimately ran the engine
    await requested(failing);
    await release(failing);
    await until(() => state().panel.status === 'error', 'error panel');
    check('failed check: no capabilities, no demo numbers', !byId('kemsApi').querySelector('tr[data-flag]') && byId('kemsApi').textContent.includes('HTTP 500')
      && [...byId('kemsApi').querySelectorAll('.kems-kpis em')].every((em) => em.textContent === '확인 불가') && !document.body.innerText.includes('55,606'));
    check('failed check keeps the API selection', state().source === 'api' && byId('model').value === 'api:kr-kpg193');
    [...byId('kemsApi').querySelectorAll('button')].find((b) => b.textContent === '다시 확인').click();
    await until(() => state().panel.status === 'ready', 'recheck');
    check('re-check recovers', panelTitle() === name('kr-kpg193') && capabilityCell('generator_contingency_available') === '제공');
    check('DC engine idle through API errors', engine.calls === 0, engine.calls);
  }

  async function timeoutScenario() {
    await window.kemsConsole.ready;
    const realFetch = window.fetch;
    window.fetch = (url, options = {}) => (String(url).startsWith('/api/grids')
      ? new Promise((resolve, reject) => options.signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError'))))
      : realFetch(url, options));
    choose('api:kr-kpg193');
    await wait(11000);
    window.fetch = realFetch;
    check('unanswered check times out', state().panel.status === 'error' && byId('kemsApi').textContent.includes('응답 시간 초과 (10초)'), state());
    check('timeout shows no capabilities', !byId('kemsApi').querySelector('tr[data-flag]'));
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

  async function shotScenario(name) {
    // Screenshot the settled state: app.css fades each screen child in (.view.on>*), and headless
    // capture can land mid-fade.
    const still = document.createElement('style');
    still.textContent = '.view.on>*,.msgs>*{animation:none!important}';
    document.head.append(still);
    await window.kemsConsole.ready;
    if (name === 'api-kpg') await chooseApi('kr-kpg193');
    if (name === 'api-mn') await chooseApi('mn-osm-35k');
    if (name === 'api-kr') await chooseApi('kr-osm-transmission');
    if (name === 'api-gate') { await chooseApi('kr-kpg193'); nav('db'); }
    if (name === 'api-error') await chooseApi('kr-kpg193');
    if (name === 'live') { document.querySelector('.mtog button[data-m="live"]').click(); await wait(4000); }
    if (name === 'ieee39') { choose('ieee39'); await wait(2500); }
    await wait(1500);
  }

  try {
    if (mode === 'parity') return finish({fingerprint: await parity(), diagnostics});
    if (mode === 'api') await apiScenario();
    if (mode === 'fail500') await initialFailure('HTTP 500');
    if (mode === 'drop') await initialFailure('EMS API 서버에 연결할 수 없습니다');
    if (mode === 'badjson') await initialFailure('응답 형식 오류');
    if (mode === 'stale') await staleScenario();
    if (mode === 'timeout') await timeoutScenario();
    if (mode === 'fonts') await fontsScenario();
    if (mode === 'shot') await shotScenario(params.get('shot'));
  } catch (error) {
    check('probe exception', false, String((error && error.stack) || error));
  }
  finish();
})();
