/* PH4-02C-2c: the console AI run on the real FastAPI/registry/PyPowSyBl paths; only the LLM is scripted.
 * Corruption, receipt ageing and delayed delivery are explicit test controls. */
(async () => {
  'use strict';
  const mode = new URLSearchParams(location.search).get('run_probe');
  const results = [];
  const check = (name, pass, detail = null) => { results.push({name, pass: !!pass, detail}); };
  const byId = (id) => document.getElementById(id);
  const text = (id) => byId(id)?.textContent || '';
  const state = () => window.kemsConsole.state();
  const refState = () => state().view.reference;
  const an = () => state().view.analysis;
  const nativeFetch = window.fetch.bind(window);
  const pause = (ms = 20) => new Promise((resolve) => setTimeout(resolve, ms));
  const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
  async function api(path, body) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 5000);
    try {
      const response = await nativeFetch('/__run_probe__/' + path, {method: body ? 'POST' : 'GET', signal: controller.signal,
        ...(body ? {headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)} : {})});
      if (!response.ok) throw new Error(`probe HTTP ${response.status}`);
      return await response.json();
    } finally { clearTimeout(timer); }
  }
  const control = (body) => api('control', body);
  const hits = async () => (await api('state')).hits;
  const lastHit = async (predicate) => (await hits()).filter(predicate).at(-1);
  const isRun = (hit) => hit.body?.analysis_context?.mode === 'run';
  async function until(predicate, label, limit = 60000) {
    const deadline = performance.now() + limit;
    while (!(await predicate())) {
      if (performance.now() > deadline) throw new Error('Timed out: ' + label);
      await pause();
    }
  }
  async function choose(grid = 'kr-kpg193') {
    byId('model').value = 'api:' + grid;
    byId('model').dispatchEvent(new Event('change'));
    await until(() => state().gridId === grid && state().view?.status === 'ready' && state().view.map.status === 'ready'
      && !['idle', 'loading'].includes(state().view.pf.status), 'grid ' + grid);
  }
  function input(value) {
    byId('kemsChatInput').value = value;
    byId('kemsChatInput').dispatchEvent(new Event('input'));
  }
  function submit(value = 'AI 분석을 실행해 주세요.') {
    input(value);
    byId('kemsChatForm').dispatchEvent(new Event('submit', {bubbles: true, cancelable: true}));
  }
  async function send(value) {
    submit(value);
    await until(() => byId('kemsChat').dataset.busy === 'false', 'chat settled');
    await pause(40);  // the microtask render that follows a run
  }
  function choice(operation = 'security', chatMode = 'run') {
    byId('kemsChatMode').value = chatMode;
    byId('kemsChatMode').dispatchEvent(new Event('change'));
    byId('kemsChatOperation').value = operation;
    byId('kemsChatOperation').dispatchEvent(new Event('change'));
  }
  function selectLine(line) {
    byId('kemsQ').value = line;
    byId('kemsQ').dispatchEvent(new Event('input'));
    const hit = [...byId('kemsRes').querySelectorAll('.msr')].find((node) => node.querySelector('b').textContent === '선로 ' + line);
    if (!hit) throw new Error('missing line ' + line);
    hit.dispatchEvent(new MouseEvent('mousedown', {bubbles: true, cancelable: true}));
  }
  const card = () => byId('kemsCard').querySelector('.kems-an-run');
  const sensButton = () => document.querySelector('#kemsSide [data-action="sensitivity"]');
  const monitorSelect = () => document.querySelector('.kems-an-mon');
  async function manualSecurity(line) {
    selectLine(line);
    card().click();
    await until(() => !['idle', 'loading'].includes(an().security.status), 'manual N-1 ' + line);
    await pause(30);
  }
  async function manualSensitivity() {
    sensButton().click();
    await until(() => !['idle', 'loading'].includes(an().sensitivity.status), 'manual Sensitivity');
    await pause(30);
  }
  async function aiRun(operation, script, line) {
    if (line) selectLine(line);
    choice(operation, 'run');
    await control({script});
    await send('AI 분석을 실행해 주세요.');
    return lastHit(isRun);
  }
  const lastAnswer = () => [...byId('kemsChatMessages').querySelectorAll('[data-role="assistant"]')].at(-1)?.textContent || '';
  const countAnswers = () => byId('kemsChatMessages').querySelectorAll('[data-role="assistant"]').length;
  const screen = () => JSON.stringify({analysis: an(), mode: state().view.mode, overlay: window.kemsConsole.map.overlay()});
  const image = () => JSON.stringify({screen: screen(), revision: refState().revision, receipts: refState().receipts, notices: refState().notices});
  const runBodyOk = (hit, operation, outage, monitored) => hit && hit.path === '/api/grids/kr-kpg193/chat'
    && Object.keys(hit.body).sort().join() === 'analysis_context,grid_id,message,request_id,session_id' && UUID.test(hit.body.request_id)
    && Object.keys(hit.body.analysis_context).sort().join() === 'mode,monitored_line_id,operation,outage_line_id,references,revision,version,view_id'
    && hit.body.analysis_context.mode === 'run' && hit.body.analysis_context.operation === operation
    && hit.body.analysis_context.outage_line_id === outage && hit.body.analysis_context.monitored_line_id === monitored
    && hit.body.analysis_context.references.security === null && hit.body.analysis_context.references.sensitivity === null;
  function hold(match, failure = false) {
    let release, seen;
    const arrived = new Promise((resolve) => { seen = resolve; });
    window.fetch = async (url, options) => {
      if (!match(String(url))) return nativeFetch(url, options);
      // Execute the real route but ignore Abort while delaying delivery of its actual response.
      const response = await nativeFetch(url, {...options, signal: undefined});
      const body = await response.text();
      return new Promise((resolve, reject) => {
        release = () => (failure ? reject(new TypeError('explicit delayed transport failure'))
          : resolve(new Response(body, {status: response.status, headers: {'Content-Type': 'application/json'}})));
        seen();
      });
    };
    return {arrived, release: () => release(), restore: () => { window.fetch = nativeFetch; }};
  }

  // 1, 4-6: a Security run, its map/panel/receipt against the manual result of the same outage, then a reference.
  async function run_security() {
    await choose();
    await manualSecurity('LINE-58-59');  // the explicit first AC of this fresh model (no hidden warm-up)
    selectLine('LINE-16-28');
    choice('security', 'run');
    check('run mode is offered with its own advertisement', refState().runAvailable && refState().mode === 'run' && !byId('kemsChatSend').disabled
      && text('kemsReferenceStatus').includes('AI 분석 실행 가능 · 사고 LINE-16-28'), text('kemsReferenceStatus'));
    const before = refState().revision, counts = (await api('state')).counts;
    await control({script: {operation: 'security', outage: 'LINE-16-28'}});
    await send();
    const hit = await lastHit(isRun);
    const after = (await api('state')).counts;
    const r = refState().receipts.security;
    const update = hit.response.ui_updates[0];
    check('security run sends only the frozen run context', runBodyOk(hit, 'security', 'LINE-16-28', null) && hit.body.analysis_context.revision === before
      && hit.body.analysis_context.view_id === refState().viewId, hit.body);
    check('the server ran the real N-1 for the run', (after.create_analysis || 0) > (counts.create_analysis || 0) && after.run_model === (counts.run_model || 0) + 1);
    check('verified result committed with AI-run provenance', an().security.status === 'ready' && an().security.origin === 'run' && an().line === 'LINE-16-28'
      && state().view.mode === 'analysis' && text('kemsSide').includes('AI 분석 실행 · 서버 검증') && lastAnswer().includes('AI 분석 실행 · 서버 검증 · 분석 결과 반영 완료'));
    check('the run receipt belongs to the request and its session', r && r.receipt.producer === 'agent_tool' && r.receipt.result_id === update.result_id
      && r.receipt.producer_request_id === hit.body.request_id && r.sessionId === hit.body.session_id && !refState().receipts.sensitivity
      && Object.keys(refState().notices).length === 0);
    check('the commit is one epoch on top of the request (bumpRevision)', refState().revision === before + 1, {before, now: refState().revision});
    choice('security', 'reference');
    await send('방금 결과를 설명해 주세요.');
    const ref = await lastHit((h) => h.body?.analysis_context?.mode === 'reference');
    check('the run receipt is referenced in the same session', ref.status === 200 && lastAnswer().includes('기존 결과 설명 · 새 계산 없음')
      && ref.body.session_id === hit.body.session_id && ref.body.analysis_context.references.security === r.receipt.result_id);
    const aiSecurity = {...an().security, origin: null}, aiOverlay = JSON.stringify(window.kemsConsole.map.overlay());
    await manualSecurity('LINE-16-28');
    const manual = await lastHit((h) => h.path === '/api/security-analysis');
    const {analysis_receipt: ignored, analysis_receipt_error: ignoredError, ...manualPayload} = manual.response;
    check('map and panel equal the manual result of the same outage', JSON.stringify(aiSecurity) === JSON.stringify({...an().security, origin: null})
      && aiOverlay === JSON.stringify(window.kemsConsole.map.overlay()) && an().security.origin === 'manual'
      && JSON.stringify(update.result) === JSON.stringify(manualPayload));
  }

  // 2: Sensitivity alone explains the N-1 result on screen and keeps it.
  async function run_sensitivity() {
    await choose();
    await manualSecurity('LINE-16-28');
    await manualSecurity('LINE-16-28');  // first AC, then an N-1 result with its manual receipt
    const security = JSON.stringify(an().security), securityReceipt = JSON.stringify(refState().receipts.security);
    choice('sensitivity', 'run');
    check('sensitivity alone targets the N-1 result on screen', text('kemsReferenceStatus').includes('사고 LINE-16-28')
      && text('kemsReferenceStatus').includes('자동 선정'), text('kemsReferenceStatus'));
    const before = refState().revision;
    await control({script: {operation: 'sensitivity', outage: 'LINE-16-28'}});
    await send();
    let hit = await lastHit(isRun);
    const r = refState().receipts.sensitivity;
    check('sensitivity run request', runBodyOk(hit, 'sensitivity', 'LINE-16-28', null), hit.body);
    check('sensitivity committed; the N-1 result and its receipt are kept', an().sensitivity.status === 'ready' && an().sensitivity.origin === 'run'
      && an().sensitivity.analysisStatus === 'COMPLETED' && an().sensitivity.candidates.length > 0 && JSON.stringify(an().security) === security
      && JSON.stringify(refState().receipts.security) === securityReceipt && JSON.parse(securityReceipt) !== null
      && r.receipt.producer === 'agent_tool' && r.receipt.monitored_selection === 'auto' && r.sessionId === hit.body.session_id
      && refState().revision === before + 1 && window.kemsConsole.map.overlay().monitored.length === 1);
    const select = monitorSelect();
    select.value = 'LINE-16-22';
    select.dispatchEvent(new Event('change'));
    await control({script: {operation: 'sensitivity', outage: 'LINE-16-28', monitored: 'LINE-16-22'}});
    await send();
    hit = await lastHit(isRun);
    check('an explicit monitored line stays explicit', runBodyOk(hit, 'sensitivity', 'LINE-16-28', 'LINE-16-22')
      && refState().receipts.sensitivity.receipt.monitored_selection === 'explicit' && an().sensitivity.monitored === 'LINE-16-22'
      && an().monitored === 'LINE-16-22', hit.body);
    document.querySelector('.kems-an-clear').click();
    await pause(50);
    choice('sensitivity', 'run');
    check('sensitivity alone is unavailable without an N-1 result', byId('kemsChatSend').disabled && text('kemsReferenceStatus').includes('N-1 결과가 필요'));
  }

  // 3, 5-6: a chain, its reference, an explicit monitored chain and a Security run that drops the old Sensitivity.
  async function run_chain() {
    await choose();
    await manualSecurity('LINE-58-59');
    const before = refState().revision;
    selectLine('LINE-16-28');
    choice('security_then_sensitivity', 'run');
    const chainBefore = refState().revision;
    await control({script: {operation: 'security_then_sensitivity', outage: 'LINE-16-28'}});
    await send();
    const hit = await lastHit(isRun);
    const {security: s, sensitivity: t} = refState().receipts;
    check('chain run request (another outage starts automatic)', runBodyOk(hit, 'security_then_sensitivity', 'LINE-16-28', null) && chainBefore >= before, hit.body);
    check('both results committed together', an().security.origin === 'run' && an().sensitivity.origin === 'run' && an().line === 'LINE-16-28'
      && an().sensitivity.analysisStatus === 'COMPLETED' && s.receipt.producer === 'agent_tool' && t.receipt.producer === 'agent_tool'
      && s.sessionId === hit.body.session_id && t.sessionId === hit.body.session_id && s.receipt.model_revision === t.receipt.model_revision
      && refState().revision === chainBefore + 1 && window.kemsConsole.map.overlay().candidates.length > 0
      && lastAnswer().includes('AI 분석 실행 · 서버 검증 · 분석 결과 반영 완료'));
    choice('security_then_sensitivity', 'reference');
    await send('두 결과를 함께 설명해 주세요.');
    const ref = await lastHit((h) => h.body?.analysis_context?.mode === 'reference');
    check('the chain receipts are referenced in the same session', ref.status === 200 && ref.body.session_id === hit.body.session_id
      && JSON.stringify(Object.values(ref.body.analysis_context.references)) === JSON.stringify([s.receipt.result_id, t.receipt.result_id]));
    const select = monitorSelect();
    select.value = 'LINE-16-22';
    select.dispatchEvent(new Event('change'));
    choice('security_then_sensitivity', 'run');
    await control({script: {operation: 'security_then_sensitivity', outage: 'LINE-16-28', monitored: 'LINE-16-22'}});
    await send();
    const explicit = await lastHit(isRun);
    check('the monitored choice of the same outage carries into a chain', runBodyOk(explicit, 'security_then_sensitivity', 'LINE-16-28', 'LINE-16-22')
      && refState().receipts.sensitivity.receipt.monitored_selection === 'explicit' && an().monitored === 'LINE-16-22', explicit.body);
    choice('security', 'run');
    await control({script: {operation: 'security', outage: 'LINE-16-28'}});
    await send();
    check('a Security run leaves no previous Sensitivity or receipt attached', an().security.origin === 'run' && an().sensitivity.status === 'idle'
      && an().monitored === '' && refState().receipts.sensitivity === null && refState().receipts.security.receipt.kind === 'security'
      && window.kemsConsole.map.overlay().candidates.length === 0);
  }

  // 8-9: not_evaluated is shown and applied; failed and not_run change nothing and keep the session.
  async function run_states() {
    await choose();
    await manualSecurity('LINE-58-59');
    await aiRun('security', {operation: 'security', outage: 'LINE-28-80'}, 'LINE-28-80');
    check('an unevaluated N-1 is applied as not assessed', an().security.verdict === 'not_assessed' && an().security.origin === 'run'
      && lastAnswer().includes('AI 분석 실행 · 서버 검증 · 평가 불가 항목 있음 (SECURITY_NOT_CONVERGED)')
      && refState().receipts.security.receipt.assessment === 'not_evaluated' && text('kemsSide').includes('평가 불가'));
    await aiRun('security_then_sensitivity', {operation: 'security_then_sensitivity', outage: 'LINE-1-2'}, 'LINE-1-2');
    check('chain without overload: N-1 applied, Sensitivity shown as NO_OVERLOAD', an().security.status === 'ready' && an().security.verdict !== 'not_assessed'
      && an().sensitivity.analysisStatus === 'NO_OVERLOAD' && an().sensitivity.candidates.length === 0
      && refState().receipts.sensitivity.receipt.assessment === 'not_evaluated' && lastAnswer().includes('(NO_OVERLOAD)'));
    await aiRun('security_then_sensitivity', {operation: 'security_then_sensitivity', outage: 'LINE-28-80'}, 'LINE-28-80');
    check('a chain ended by an unevaluated N-1 applies the N-1 only', an().security.verdict === 'not_assessed' && an().sensitivity.status === 'idle'
      && refState().receipts.sensitivity === null && lastAnswer().includes('(SECURITY_NOT_CONVERGED)'));
    selectLine('LINE-16-28');
    choice('security', 'run');
    let snapshot = image();
    await control({script: {steps: [{tool: 'line_contingency', args: {outage_line_id: 'LINE-58-59'}}]}});
    await send();
    const failed = await lastHit(isRun);
    check('failed: screen, receipts, revision and session unchanged', failed.response.analysis.status === 'failed' && image() === snapshot
      && lastAnswer().includes('분석 실행 실패 (OUTAGE_LINE_MISMATCH) · 기존 분석 결과 유지') && !text('kemsChatMessages').includes('새 서버 대화'));
    snapshot = image();
    await control({script: {steps: [{text: '계산을 완료했습니다. 위반 0건입니다.'}]}});
    await send();
    const notRun = await lastHit(isRun);
    check('not_run: a text-only reply changes nothing', notRun.response.analysis.status === 'not_run' && image() === snapshot
      && lastAnswer().includes('AI 분석 실행 · Tool 미실행 · 기존 분석 결과 유지'));
    await control({script: {operation: 'security', outage: 'LINE-16-28'}});
    await send();
    const next = await lastHit(isRun);
    check('the session survives failed and not_run answers', next.body.session_id === failed.body.session_id && notRun.body.session_id === failed.body.session_id
      && an().security.origin === 'run' && an().line === 'LINE-16-28');
  }

  // 7: the first AC of a fresh model fails closed; only a new request computes again.
  async function run_first_ac() {
    await choose();
    selectLine('LINE-16-28');
    choice('security', 'run');
    const snapshot = image();
    await control({script: {operation: 'security', outage: 'LINE-16-28'}});
    await send();
    const hit = await lastHit(isRun);
    check('the first AC run is refused and nothing is applied', hit.response.analysis.status === 'failed' && hit.response.analysis.reason_code === 'MODEL_REVISION_CHANGED'
      && image() === snapshot && an().security.status === 'idle' && lastAnswer().includes('분석 실행 실패 (MODEL_REVISION_CHANGED)'));
    check('no hidden rerun', (await api('state')).counts.run_model === 1);
    await control({script: {operation: 'security', outage: 'LINE-16-28'}});
    await send();
    check('a new request computes on the settled model', an().security.origin === 'run' && refState().receipts.security?.receipt.producer === 'agent_tool'
      && (await api('state')).counts.run_model === 2);
  }

  // 10-11: one corrupt part, a changed receipt or an expired receipt rejects the whole response.
  async function run_reject() {
    await choose();
    await manualSecurity('LINE-58-59');
    await aiRun('security_then_sensitivity', {operation: 'security_then_sensitivity', outage: 'LINE-16-28'}, 'LINE-16-28');
    const session = (await lastHit(isRun)).body.session_id;
    const base = Date.now() - 7200000;
    const at = (ms) => new Date(base + ms).toISOString();
    const corruptions = [
      ['second ui_update result', [[['ui_updates', 1, 'result', 'outage_line_id'], 'LINE-99-99']], false],
      ['second ui_update type', [[['ui_updates', 1, 'type'], 'security']], false],
      ['second ui_update result_id', [[['ui_updates', 1, 'result_id'], '20000000-0000-4000-8000-0000000000c2']], false],
      ['receipt producer', [[['analysis', 'results', 0, 'producer'], 'manual']], false],
      ['receipt tool_call_id', [[['analysis', 'results', 1, 'tool_call_id'], '']], false],
      ['receipt model revision', [[['analysis', 'results', 1, 'model_revision'], 'f'.repeat(64)]], false],
      ['expired receipts', [[['analysis', 'results', 0, 'created_at'], at(0)], [['analysis', 'results', 0, 'expires_at'], at(600000)],
        [['analysis', 'results', 1, 'created_at'], at(0)], [['analysis', 'results', 1, 'expires_at'], at(600000)]], true],
    ];
    for (const [name, patches, expired] of corruptions) {
      const snapshot = image(), answers = countAnswers();
      await control({rule: {patches}, script: {operation: 'security_then_sensitivity', outage: 'LINE-16-28'}});
      await send();
      const hit = await lastHit(isRun);
      check(`${name}: whole response rejected, nothing changed`, image() === snapshot && countAnswers() === answers && hit.response.analysis.status === 'completed'
        && text('kemsChatStatus').includes(expired ? '분석 결과가 만료되었습니다' : '전체를 거부했습니다') && hit.body.session_id === session
        && !text('kemsChatMessages').includes('새 서버 대화'), text('kemsChatStatus'));
    }
    const revision = refState().revision;
    await control({script: {operation: 'security_then_sensitivity', outage: 'LINE-16-28'}});
    await send();
    check('the kept session runs again', (await lastHit(isRun)).body.session_id === session && refState().revision === revision + 1
      && lastAnswer().includes('AI 분석 실행 · 서버 검증 · 분석 결과 반영 완료'));
  }

  // 12, 15: a late success or failure after a grid switch, chat reset, analysis clear or reload is discarded.
  async function run_late() {
    await choose();
    await manualSecurity('LINE-58-59');
    const changes = {
      grid: async () => { await choose('mn-osm-35k'); await choose(); },
      reset: async () => byId('kemsChatReset').click(),
      clear: async () => document.querySelector('.kems-an-clear').click(),
      reload: () => choose(),
    };
    for (const [name, change] of Object.entries(changes)) {
      for (const failure of [false, true]) {
        if (an().security.status !== 'ready') await manualSecurity('LINE-58-59');  // the clear button needs a result
        selectLine('LINE-16-28');
        choice('security', 'run');
        await control({script: {operation: 'security', outage: 'LINE-16-28'}});
        const held = hold((url) => url.endsWith('/chat'), failure);
        try {
          submit();
          await held.arrived;
          await change();
          await pause(40);
          const after = screen(), messages = text('kemsChatMessages');
          held.release();
          await pause(80);
          check(`${name}: late ${failure ? 'failure' : 'success'} discarded`, screen() === after && text('kemsChatMessages') === messages
            && byId('kemsChat').dataset.busy === 'false' && an().security.origin !== 'run' && !refState().runPending);
        } finally { held.restore(); }
      }
    }
  }

  // 13-14: a pending run locks the manual N-1/Sensitivity, the monitored line and the chat selectors.
  async function run_locks() {
    await choose();
    await manualSecurity('LINE-16-28');
    await manualSensitivity();
    selectLine('LINE-16-28');
    choice('security', 'run');
    for (const outcome of ['success', 'failure']) {
      if (an().security.status === 'ready' && an().sensitivity.status !== 'ready') await manualSensitivity();
      await control({script: {operation: 'security', outage: 'LINE-16-28'}});
      const held = hold((url) => url.endsWith('/chat'), outcome === 'failure');
      try {
        submit();
        await held.arrived;
        await pause(40);
        const manualHits = (await hits()).filter((h) => h.body === null).length;
        const revision = refState().revision, monitored = an().monitored;
        check(`${outcome}: manual controls are locked while the run is pending`, card().disabled && sensButton().disabled && monitorSelect().disabled
          && [...document.querySelectorAll('[data-list="alternatives"] button')].every((b) => b.disabled)
          && document.querySelector('[data-lock="run"]') && byId('kemsChatMode').disabled && byId('kemsChatOperation').disabled && refState().runPending);
        card().click();
        sensButton().click();
        const select = monitorSelect();
        select.value = 'LINE-16-22';
        select.dispatchEvent(new Event('change'));
        choice('sensitivity', 'reference');
        await pause(60);
        check(`${outcome}: forced manual actions and choices change nothing`, (await hits()).filter((h) => h.body === null).length === manualHits
          && refState().revision === revision && an().monitored === monitored && refState().mode === 'run' && refState().operation === 'security'
          && byId('kemsChat').dataset.busy === 'true' && refState().runPending);
        held.release();
        await until(() => byId('kemsChat').dataset.busy === 'false', 'locked run settled');
        await pause(60);
        check(`${outcome}: everything is unlocked when the run ends`, !card().disabled && !monitorSelect().disabled && !sensButton().disabled
          && !document.querySelector('[data-lock="run"]') && !refState().runPending && !byId('kemsChatMode').disabled && !byId('kemsChatOperation').disabled
          && (outcome === 'success' ? an().security.origin === 'run' : byId('kemsChatStatus').classList.contains('kems-err')));
      } finally { held.restore(); }
    }
  }

  // Another line card chosen while a run is pending: the run keeps its frozen outage, the screen says so.
  async function run_selection() {
    await choose();
    await manualSecurity('LINE-58-59');
    for (const operation of ['security', 'sensitivity']) {
      const lineCard = operation === 'security' ? 'LINE-16-28' : 'LINE-58-59';
      selectLine(lineCard);
      // Security follows the line card; Sensitivity alone follows the N-1 result on screen, not the card.
      const outage = operation === 'security' ? lineCard : an().line;
      choice(operation, 'run');
      await control({script: {operation, outage}});
      const held = hold((url) => url.endsWith('/chat'));
      try {
        submit();
        await held.arrived;
        await pause(40);
        const revision = refState().revision;
        selectLine('LINE-1-2');  // the map stays usable while the run is pending
        await pause(40);
        const lock = document.querySelector('[data-lock="run"]')?.textContent || '';
        check(`${operation}: another line card can be opened while the run is pending`, state().view.selection?.id === 'LINE-1-2'
          && card().getAttribute('data-line') === 'LINE-1-2' && card().disabled && refState().revision === revision && refState().runPending);
        check(`${operation}: status and lock name the outage being computed, not the new card`,
          text('kemsReferenceStatus').includes(`AI 분석 실행 중 · 사고 ${outage}`) && !text('kemsReferenceStatus').includes('사고 LINE-1-2')
          && lock.includes(`사고 ${outage}`), {status: text('kemsReferenceStatus'), lock});
        held.release();
        await until(() => byId('kemsChat').dataset.busy === 'false', 'selection run settled');
        await pause(60);
        const hit = await lastHit(isRun);
        const overlay = window.kemsConsole.map.overlay();
        check(`${operation}: the result is applied to the requested outage only`, runBodyOk(hit, operation, outage, null)
          && an().line === outage && an()[operation].origin === 'run' && JSON.stringify(overlay.outage) === JSON.stringify([outage])
          && refState().receipts[operation].receipt.outage_line_id === outage
          && (operation !== 'security' || lastAnswer().includes(`사고 선로 ${outage}`)), {line: an().line, outage: overlay.outage});
        check(`${operation}: the open card says which outage the shown result belongs to`, state().view.selection?.id === 'LINE-1-2'
          && (byId('kemsCard').textContent || '').includes(`표시 중인 결과: ${outage} 사고`) && !card().disabled
          && text('kemsReferenceStatus').includes('AI 분석 실행 가능 · 사고'), byId('kemsCard').textContent);
      } finally { held.restore(); }
    }
  }

  // 16: the 60-second limit keeps the screen and says the server may still be computing.
  async function run_timeout() {
    await choose();
    await manualSecurity('LINE-58-59');
    selectLine('LINE-16-28');
    choice('security', 'run');
    const before = screen();
    await control({script: {operation: 'security', outage: 'LINE-16-28'}});
    const realTimer = window.setTimeout;
    window.setTimeout = (fn, ms, ...args) => realTimer(fn, ms === 60000 ? 100 : ms, ...args);
    const held = hold((url) => url.endsWith('/chat'));
    try {
      submit();
      await until(() => byId('kemsChat').dataset.busy === 'false', 'run timeout', 5000);
      await held.arrived;
      const messages = text('kemsChatMessages');
      held.release();
      await pause(80);
      check('timeout keeps the screen and names the still-running server', text('kemsChatStatus').includes('서버 계산은 계속될 수 있으며')
        && screen() === before && messages === text('kemsChatMessages') && !document.querySelector('[data-lock="run"]') && !card().disabled);
      check('HTTP abandonment did not stop the server run', (await lastHit(isRun)).response.analysis.status === 'completed');
    } finally { held.restore(); window.setTimeout = realTimer; }
  }

  // 17-18: basic chat and the C-1 reference keep working; Mongolia and Korea never see a run.
  async function run_regression() {
    await choose();
    choice('security', 'basic');
    await send('기본 채팅');
    const basic = (await hits()).filter((h) => h.body).at(-1);
    check('basic chat keeps the legacy body in a run-capable view', Object.keys(basic.body).sort().join() === 'grid_id,message,session_id'
      && lastAnswer().includes('고정 기본 채팅 응답'));
    await manualSecurity('LINE-16-28');
    await manualSecurity('LINE-16-28');
    await manualSensitivity();
    choice('security_then_sensitivity', 'reference');
    await send('두 결과를 설명해 주세요.');
    check('manual receipts and the reference still work', lastAnswer().includes('기존 결과 설명 · 새 계산 없음')
      && refState().receipts.security.receipt.producer === 'manual');
    await choose('mn-osm-35k');
    check('Mongolia offers no run or reference even when advertised', !refState().runAvailable && byId('kemsChatMode').closest('.kems-reference').hidden);
    choice('security', 'run');
    await send('몽골 기본 채팅');
    const mn = (await hits()).filter((h) => h.body).at(-1);
    check('Mongolia chat stays legacy', mn.path === '/api/grids/mn-osm-35k/chat' && !mn.body.analysis_context && !Object.keys(mn.headers).length);
    const before = (await hits()).length;
    await choose('kr-osm-transmission');
    submit('차단');
    await pause(60);
    check('Korea performs no chat or analysis', before === (await hits()).length && byId('kemsChatInput').disabled);
  }

  // 19: reference advertised without run: the run stays hidden and is never sent.
  async function gating_reference() {
    await choose();
    const option = byId('kemsChatMode').querySelector('option[value="run"]');
    check('reference without a run advertisement hides the run', !byId('kemsChatMode').closest('.kems-reference').hidden && option.hidden && option.disabled
      && refState().runAvailable === false);
    await manualSecurity('LINE-58-59');
    selectLine('LINE-16-28');
    choice('security', 'run');
    check('a forced run choice is refused', refState().mode !== 'run');
    await send('기본 채팅');
    check('no run context is ever sent', (await hits()).every((h) => h.body?.analysis_context?.mode !== 'run') && (await api('state')).counts.run_model === undefined);
  }

  // 19: nothing advertised (production today): no v1 control, legacy requests only.
  async function gating_none() {
    await choose();
    check('no advertisement shows no v1 control', byId('kemsChatMode').closest('.kems-reference').hidden && refState().runAvailable === false);
    await manualSecurity('LINE-58-59');
    await send('기본 채팅');
    const all = await hits();
    check('manual and chat requests stay legacy', all.filter((h) => !h.body).every((h) => !Object.keys(h.headers).length)
      && Object.keys(all.filter((h) => h.body).at(-1).body).sort().join() === 'grid_id,message,session_id');
  }

  try {
    await window.kemsConsole.ready;
    await ({run_security, run_sensitivity, run_chain, run_states, run_first_ac, run_reject, run_late, run_locks, run_selection,
      run_timeout, run_regression, gating_reference, gating_none})[mode]();
  } catch (error) {
    check('probe exception', false, String(error.stack || error));
  } finally {
    await control({report: {case: mode, results, errors: window.__runErrors}});
  }
})();
