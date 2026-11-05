/* PH4-01 only: real HTTP chats use scripted server graphs, while explicitly labelled
 * fetch stand-ins exercise malformed and abort-ignoring network behavior. */
(async () => {
  'use strict';
  const mode = new URLSearchParams(location.search).get('chat_probe');
  const results = [];
  const check = (name, pass, detail = null) => results.push({name, pass: !!pass, detail});
  const byId = (id) => document.getElementById(id);
  const text = (id) => byId(id)?.textContent || '';
  const visible = (node) => !!node && node.getClientRects().length > 0;
  const pause = (ms = 20) => new Promise(resolve => setTimeout(resolve, ms));
  const nativeFetch = window.fetch.bind(window);
  const api = async (path, body) => {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 5000);
    try {
      return await (await nativeFetch('/__chat_probe__/' + path,
        {method: body ? 'POST' : 'GET', ...(body ? {headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)} : {}), signal: controller.signal})).json();
    } finally { clearTimeout(timer); }
  };
  const control = (body) => api('control', body);
  const hits = async () => (await api('state')).hits;
  async function until(predicate, label, limit = 30000) {
    const deadline = performance.now() + limit;
    while (!(await predicate())) {
      if (performance.now() > deadline) throw new Error(`Timed out: ${label}`);
      await pause();
    }
  }
  const state = () => window.kemsConsole.state();
  const chat = () => byId('kemsChat');
  const input = () => byId('kemsChatInput');
  const messages = (role) => [...byId('kemsChatMessages').querySelectorAll(`[data-role="${role}"]`)];
  const lastAnswer = () => messages('assistant').at(-1)?.textContent || '';
  const errorNotice = (pattern) => byId('kemsChatStatus').classList.contains('kems-err')
    && pattern.test(text('kemsChatStatus')) && messages('system').some(message => pattern.test(message.textContent));
  const setMessage = (value) => { input().value = value; input().dispatchEvent(new Event('input', {bubbles: true})); };
  const submit = (value) => { setMessage(value); byId('kemsChatForm').dispatchEvent(new Event('submit', {bubbles: true, cancelable: true})); };
  async function send(value) {
    submit(value);
    await until(() => chat().dataset.busy === 'false', `chat ${value}`);
    return lastAnswer();
  }
  async function choose(id, waitMap = true) {
    const select = byId('model');
    select.value = id.startsWith('api:') ? id : `api:${id}`;
    select.dispatchEvent(new Event('change'));
    await until(() => state().gridId === id.replace(/^api:/, '') && state().view?.status === 'ready', `grid ${id}`);
    if (waitMap) await until(() => ['ready', 'error', 'unavailable'].includes(state().view.map.status)
      && !['idle', 'loading'].includes(state().view.pf.status), `map/PF ${id}`);
  }
  async function demo() {
    byId('model').value = 'six';
    byId('model').dispatchEvent(new Event('change'));
    await until(() => state().source === 'demo', 'demo');
  }
  async function sessions() {
    await choose('kr-kpg193');
    check('chat visible on KPG', visible(chat()) && chat().dataset.gridId === 'kr-kpg193' && !input().disabled);
    const unsafe = '<img src=x onerror="window.__ph4Xss=true"><script>window.__ph4Xss=true</script>';
    await send(unsafe);
    check('user and AI text remain literal', messages('user')[0].textContent.includes(unsafe) && lastAnswer().includes(unsafe)
      && !byId('kemsChatMessages').querySelector('img,script') && !window.__ph4Xss);
    check('first real KPG server turn', lastAnswer().includes('kr-kpg193 · turn=1'));
    await send('hello-kpg-two');
    check('same grid server context continues', lastAnswer().includes('turn=2') && lastAnswer().includes(unsafe));
    const kpg = (await hits()).at(-1).body.session_id;
    const savedKpg = text('kemsChatMessages');
    await choose('mn-osm-35k');
    check('Mongolia starts separately', messages('user').length === 0 && messages('assistant').length === 0);
    await send('hello-mn-one');
    check('real Mongolia route and new server history', lastAnswer().includes('mn-osm-35k · turn=1') && !lastAnswer().includes('hello-kpg'));
    const mn = (await hits()).at(-1).body.session_id;
    check('different session IDs per grid', kpg !== mn);
    await choose('kr-kpg193');
    check('KPG completed transcript restored', text('kemsChatMessages') === savedKpg);
    await send('hello-kpg-three');
    check('restored session still reaches server context', lastAnswer().includes('turn=3') && (await hits()).at(-1).body.session_id === kpg);
    byId('kemsChatReset').click();
    check('reset clears active transcript', messages('user').length === 0 && messages('assistant').length === 0);
    await send('hello-kpg-after-reset');
    check('reset rotates session and clears server context', lastAnswer().includes('turn=1') && !lastAnswer().includes('hello-kpg-two')
      && (await hits()).at(-1).body.session_id !== kpg);
    await choose('mn-osm-35k');
    check('KPG reset keeps Mongolia transcript', lastAnswer().includes('hello-mn-one'));
    await send('hello-mn-two');
    check('Mongolia session preserved', lastAnswer().includes('turn=2') && (await hits()).at(-1).body.session_id === mn);
    setMessage('composition draft');
    const count = (await hits()).length;
    input().dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', isComposing: true, bubbles: true, cancelable: true}));
    input().dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', shiftKey: true, bubbles: true, cancelable: true}));
    await pause(80);
    check('IME and Shift+Enter do not submit', (await hits()).length === count);
    setMessage('hello-enter');
    input().dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true, cancelable: true}));
    await until(() => chat().dataset.busy === 'false', 'Enter request');
    check('Enter submits once', (await hits()).length === count + 1 && lastAnswer().includes('hello-enter'));
  }
  function heldFetch({reject = false} = {}) {
    let release, seen, body;
    const requested = new Promise(resolve => { seen = resolve; });
    window.fetch = (url, options) => {
      if (!String(url).endsWith('/chat')) return nativeFetch(url, options);
      body = JSON.parse(options.body);
      return new Promise((resolve, fail) => {
        // Intentionally ignores abort: epoch checks must protect the application.
        release = () => reject ? fail(new TypeError('fixture late network failure')) : resolve(new Response(JSON.stringify({
          grid_id: body.grid_id, session_id: body.session_id, answer: 'LATE-RESPONSE-MUST-DROP', ui_updates: [],
        }), {status: 200, headers: {'Content-Type': 'application/json'}}));
        seen();
      });
    };
    return {requested, release: () => release(), body: () => body, restore: () => { window.fetch = nativeFetch; }};
  }
  async function races() {
    await choose('kr-kpg193');
    await control({rule: {hold: true}});
    submit('hello-held-real');
    await until(() => chat().dataset.busy === 'true', 'real pending');
    await until(async () => (await hits()).length === 1, 'real chat reached server');
    await until(() => byId('kemsChatSend').disabled, 'send disabled');
    submit('duplicate-must-not-send');
    await pause(80);
    const during = await hits();
    check('duplicate submit blocked', during.length === 1 && during[0].body.message === 'hello-held-real');
    const interruptedId = during[0].body.session_id;
    await choose('mn-osm-35k');
    await control({release: true});
    await send('hello-mn-after-switch');
    check('held KPG response not mixed into Mongolia', !text('kemsChatMessages').includes('hello-held-real'));
    await choose('kr-kpg193');
    await send('hello-kpg-after-switch');
    check('interrupted KPG session rotated after server already finished', lastAnswer().includes('turn=1')
      && (await hits()).at(-1).body.session_id !== interruptedId && !lastAnswer().includes('hello-held-real'));
    for (const reject of [false, true]) {
      const held = heldFetch({reject});
      submit('hello-abort-ignored');
      await held.requested;
      await choose('mn-osm-35k');
      const snapshot = text('kemsChatMessages');
      held.release();
      await pause(100);
      held.restore();
      check(`late ${reject ? 'failure' : 'success'} ignoring abort is discarded`, text('kemsChatMessages') === snapshot);
      await choose('kr-kpg193');
    }
    const held = heldFetch();
    submit('hello-reset-pending');
    await held.requested;
    const old = held.body().session_id;
    byId('kemsChatReset').click();
    held.release();
    await pause(100);
    held.restore();
    check('reset drops delayed response and clears busy state', !text('kemsChatMessages').includes('LATE-RESPONSE')
      && messages('user').length === 0 && chat().dataset.busy === 'false');
    await send('hello-after-pending-reset');
    check('reset pending rotates session', (await hits()).at(-1).body.session_id !== old && lastAnswer().includes('turn=1'));
    const disabled = heldFetch();
    submit('hello-capability-change');
    await disabled.requested;
    await control({disabled: 'kr-kpg193'});
    document.querySelector('#kemsSide .kems-sidehead button').click();
    await until(() => input().disabled && chat().dataset.busy === 'false', 'capability disabled');
    disabled.release();
    await pause(100);
    disabled.restore();
    check('capability loss abandons pending response', !text('kemsChatMessages').includes('LATE-RESPONSE')
      && input().disabled && chat().dataset.busy === 'false');
  }
  async function errors() {
    for (const [grid, code] of [['kr-kpg193', '500'], ['mn-osm-35k', '503']]) {
      await choose(grid);
      await send('fixture-error');
      check(`${grid} real backend ${code} displayed`, errorNotice(new RegExp(`HTTP ${code}`))
        && messages('assistant').length === 0 && !byId('kemsChatSend').disabled);
      const failed = (await hits()).at(-1).body.session_id;
      await send('hello-recovered');
      check(`${grid} recovery starts clean session`, lastAnswer().includes('turn=1') && (await hits()).at(-1).body.session_id !== failed);
    }
    await choose('kr-kpg193');
    for (const mode of ['wrong-grid', 'wrong-session', 'empty', 'bad-json']) {
      byId('kemsChatReset').click();
      await control({rule: {mode}});
      await send(`hello-invalid-${mode}`);
      check(`${mode} rejected`, messages('assistant').length === 0 && chat().dataset.busy === 'false'
        && errorNotice(mode === 'bad-json' ? /응답 형식/ : /계통·세션|답변 형식/));
      const failed = (await hits()).at(-1).body.session_id;
      await send(`hello-recover-${mode}`);
      check(`${mode} rotates server session`, lastAnswer().includes('turn=1') && (await hits()).at(-1).body.session_id !== failed);
    }
    byId('kemsChatReset').click();
    window.fetch = (url, opts) => String(url).endsWith('/chat') ? Promise.reject(new TypeError('fixture offline')) : nativeFetch(url, opts);
    await send('hello-offline');
    window.fetch = nativeFetch;
    check('network failure ends loading with notice', chat().dataset.busy === 'false' && messages('assistant').length === 0
      && errorNotice(/연결할 수 없습니다/));
  }
  async function gating() {
    await choose('kr-osm-transmission');
    const before = (await hits()).length;
    check('Korea chat disabled', input().disabled && byId('kemsChatSend').disabled && visible(chat()));
    submit('forbidden-korea');
    await pause(80);
    check('Korea sends zero chat requests', (await hits()).length === before);
    await control({disabled: 'kr-kpg193'});
    await choose('kr-kpg193');
    check('catalog false blocks supported grid too', input().disabled && byId('kemsChatSend').disabled);
    submit('forbidden-capability');
    await pause(80);
    check('disabled capability sends zero chat requests', (await hits()).length === before);
    await control({disabled: null});
    await choose('mn-osm-35k');
    await send('hello-before-demo');
    const count = (await hits()).length;
    await demo();
    check('API chat hidden in original demo', !visible(chat()) && visible(byId('form')) && visible(byId('msgs')));
    const messagesBefore = byId('msgs').children.length;
    byId('q').value = '현재 계통 상태 알려줘';
    byId('form').requestSubmit();
    await until(() => byId('msgs').children.length >= messagesBefore + 2 && !busy, 'demo answer', 20000);
    check('original Artifact chat works without API chat', byId('msgs').children.length >= messagesBefore + 2
      && (await hits()).length === count);
    await choose('kr-osm-transmission');
  }
  async function coexistence() {
    await choose('kr-kpg193');
    const field = input();
    setMessage('draft kept while analysis renders');
    const query = byId('kemsQ');
    query.value = 'LINE-16-28';
    query.dispatchEvent(new Event('input'));
    const hit = [...byId('kemsRes').querySelectorAll('.msr')].find(node => node.querySelector('b').textContent === '선로 LINE-16-28');
    check('real KPG line selected', !!hit);
    hit.dispatchEvent(new MouseEvent('mousedown', {bubbles: true, cancelable: true}));
    byId('kemsCard').querySelector('.kems-an-run').click();
    await until(() => state().view.analysis.security.status === 'ready', 'real N-1', 45000);
    document.querySelector('#kemsSide [data-action="sensitivity"]').click();
    await until(() => state().view.analysis.sensitivity.status === 'ready', 'real Sensitivity', 45000);
    check('draft and composer node survive analysis rerenders', input() === field && input().value === 'draft kept while analysis renders');
    const analysis = JSON.stringify(state().view.analysis);
    const overlay = JSON.stringify(window.kemsConsole.map.overlay());
    const side = text('kemsSide');
    const alternateController = new AbortController();
    const alternateTimer = setTimeout(() => alternateController.abort(), 30000);
    let alternate;
    try {
      const response = await nativeFetch('/api/security-analysis?outage_line_id=LINE-1-2&grid_id=kr-kpg193', {signal: alternateController.signal});
      if (!response.ok) throw new Error(`Alternate real N-1 failed: HTTP ${response.status}`);
      alternate = await response.json();
    } finally { clearTimeout(alternateTimer); }
    check('ignored ui_updates contains a different valid real N-1 result', alternate.outage_line_id === 'LINE-1-2'
      && alternate.outage_line_id !== state().view.analysis.line && alternate.post_status === 'CONVERGED'
      && alternate.analysis_status === 'COMPLETED' && alternate.violation_assessment_available === true
      && Array.isArray(alternate.violations) && alternate.violated_equipment_count === alternate.violations.length);
    await control({rule: {mode: 'ui-updates', result: alternate}});
    await send('hello-with-ignored-update');
    check('chat leaves real N-1/Sensitivity state intact', JSON.stringify(state().view.analysis) === analysis && text('kemsSide') === side);
    check('ui_updates do not change map overlays', JSON.stringify(window.kemsConsole.map.overlay()) === overlay);
    check('analysis and chat coexist in panel', visible(document.querySelector('#kemsSide [data-sec="n1"]')) && visible(chat())
      && chat().closest('.panel.chat') === byId('kemsSide').closest('.panel.chat'));
    check('chat request has no automatic analysis context', JSON.stringify(Object.keys((await hits()).at(-1).body).sort())
      === JSON.stringify(['grid_id', 'message', 'session_id']) && (await hits()).at(-1).body.message === 'hello-with-ignored-update');
  }
  async function timeout() {
    await choose('kr-kpg193');
    let expiredSession;
    window.fetch = (url, opts) => String(url).endsWith('/chat') ? new Promise((resolve, reject) => {
      expiredSession = JSON.parse(opts.body).session_id;
      opts.signal.addEventListener('abort', () => reject(new DOMException('fixture timeout', 'AbortError')));
    }) : nativeFetch(url, opts);
    await send('hello-timeout');
    window.fetch = nativeFetch;
    window.__chatRestoreTimeout();
    check('60-second timeout path releases composer and reports failure', chat().dataset.busy === 'false' && !input().disabled
      && messages('assistant').length === 0 && errorNotice(/시간.*초과/));
    await send('hello-after-timeout');
    check('timeout retires the old session before a real recovery request', typeof expiredSession === 'string'
      && (await hits()).at(-1).body.session_id !== expiredSession && lastAnswer().includes('turn=1')
      && !lastAnswer().includes('hello-timeout') && !byId('kemsChatStatus').classList.contains('kems-err'));
  }
  try {
    await until(() => !!window.kemsConsole, 'console initialized');
    await window.kemsConsole.ready;
    await ({sessions, races, errors, gating, coexistence, timeout})[mode]();
  } catch (error) {
    check('probe completed', false, String(error.stack || error));
  } finally {
    window.fetch = nativeFetch;
    await control({release: true, report: {case: mode, results, errors: window.__chatErrors || []}});
  }
})();
