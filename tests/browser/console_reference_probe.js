/* C-1: real manual REST/registry/reference paths, explicit wire corruption and delayed delivery. */
(async () => {
  'use strict';
  const mode = new URLSearchParams(location.search).get('reference_probe');
  const results = [];
  const check = (name, pass, detail = null) => { results.push({name, pass: !!pass, detail}); };
  const byId = (id) => document.getElementById(id);
  const text = (id) => byId(id)?.textContent || '';
  const state = () => window.kemsConsole.state();
  const refState = () => state().view.reference;
  const nativeFetch = window.fetch.bind(window);
  const pause = (ms = 20) => new Promise(resolve => setTimeout(resolve, ms));
  async function api(path, body) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 5000);
    try {
      const response = await nativeFetch('/__reference_probe__/' + path, {method: body ? 'POST' : 'GET', signal: controller.signal,
        ...(body ? {headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)} : {})});
      if (!response.ok) throw new Error(`probe HTTP ${response.status}`);
      return await response.json();
    } finally { clearTimeout(timer); }
  }
  const control = (body) => api('control', body);
  async function until(predicate, label, limit = 45000) {
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
  function submit(value = '저장된 결과를 설명해 주세요.') {
    input(value);
    byId('kemsChatForm').dispatchEvent(new Event('submit', {bubbles: true, cancelable: true}));
  }
  async function send(value) {
    submit(value);
    await until(() => byId('kemsChat').dataset.busy === 'false', 'chat settled');
  }
  function choice(operation = 'security', mode = 'reference') {
    byId('kemsChatMode').value = mode;
    byId('kemsChatMode').dispatchEvent(new Event('change'));
    byId('kemsChatOperation').value = operation;
    byId('kemsChatOperation').dispatchEvent(new Event('change'));
  }
  function selectLine(line) {
    byId('kemsQ').value = line;
    byId('kemsQ').dispatchEvent(new Event('input'));
    const hit = [...byId('kemsRes').querySelectorAll('.msr')].find(node => node.querySelector('b').textContent === '선로 ' + line);
    if (!hit) throw new Error('missing line ' + line);
    hit.dispatchEvent(new MouseEvent('mousedown', {bubbles: true, cancelable: true}));
  }
  function startSecurity(line = 'LINE-16-28') {
    selectLine(line);
    byId('kemsCard').querySelector('.kems-an-run').click();
  }
  async function security(line) {
    startSecurity(line);
    await until(() => !['idle', 'loading'].includes(state().view.analysis.security.status), 'Security');
    check('canonical Security displayed', state().view.analysis.security.status === 'ready', state().view.analysis.security);
  }
  async function readySecurity() {
    await security();
    if (!refState().receipts.security) {
      check('explicit second click follows real first AC registration refusal', text('kemsReferenceStatus').includes('모델 상태가 변경'));
      await security();
    }
    if (!refState().receipts.security) throw new Error('no real security receipt: ' + text('kemsReferenceStatus'));
  }
  async function sensitivity() {
    document.querySelector('#kemsSide [data-action="sensitivity"]').click();
    await until(() => !['idle', 'loading'].includes(state().view.analysis.sensitivity.status), 'Sensitivity');
    check('canonical Sensitivity displayed', state().view.analysis.sensitivity.status === 'ready');
  }
  const analysisImage = () => JSON.stringify({analysis: state().view.analysis, revision: refState().revision,
    overlay: window.kemsConsole.map.overlay(), side: text('kemsSide'), zoom: window.kemsConsole.map.viewBox()});
  const lastAnswer = () => [...byId('kemsChatMessages').querySelectorAll('[data-role="assistant"]')].at(-1)?.textContent || '';
  const countAnswers = () => byId('kemsChatMessages').querySelectorAll('[data-role="assistant"]').length;
  const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

  async function receipts() {
    await choose();
    await security();
    check('first AC shows manual result without a receipt', !refState().receipts.security && state().view.analysis.security.status === 'ready');
    check('first AC notice does not promise success', text('kemsReferenceStatus').includes('수동 분석 결과는 표시하지만'));
    choice();
    check('reference disabled without receipt', byId('kemsChatSend').disabled);
    await security();
    const sec = refState().receipts.security;
    await sensitivity();
    const sens = refState().receipts.sensitivity;
    check('consecutive real receipts have one owner and model', sec && sens && sec.sessionId === sens.sessionId
      && sec.receipt.model_revision === sens.receipt.model_revision);
    for (const operation of ['security', 'sensitivity', 'security_then_sensitivity']) {
      choice(operation);
      const before = analysisImage(), counts = (await api('state')).counts;
      const inputNode = byId('kemsChatInput');
      await send();
      const after = await api('state');
      const body = after.hits.at(-1).body;
      check(operation + ' references without graph, Tool or physics calls', JSON.stringify(counts) === JSON.stringify(after.counts));
      check(operation + ' preserves analysis, panel, revision, zoom and composer', before === analysisImage() && inputNode === byId('kemsChatInput'));
      check(operation + ' request uses only allowed fields', Object.keys(body).sort().join() === 'analysis_context,grid_id,message,request_id,session_id'
        && Object.keys(body.analysis_context).sort().join() === 'mode,monitored_line_id,operation,outage_line_id,references,revision,version,view_id'
        && UUID.test(body.request_id) && UUID.test(body.analysis_context.view_id) && body.analysis_context.monitored_line_id === null);
      const expected = operation === 'security' ? [sec.receipt.result_id, null] : operation === 'sensitivity' ? [null, sens.receipt.result_id]
        : [sec.receipt.result_id, sens.receipt.result_id];
      check(operation + ' sends exactly the stored IDs', JSON.stringify(Object.values(body.analysis_context.references)) === JSON.stringify(expected));
      check(operation + ' marks explanation as no new calculation', lastAnswer().includes('기존 결과 설명 · 새 계산 없음'));
    }
    const unsafe = '<img src=x onerror="window.__referenceXss=true"><script>window.__referenceXss=true</script>';
    await control({rule: {patches: [[['answer'], unsafe]]}});
    await send(unsafe);
    check('reference text cannot create HTML', lastAnswer().includes(unsafe) && !window.__referenceXss
      && !byId('kemsChatMessages').querySelector('img,script'));
    const monitored = document.querySelector('.kems-an-mon');
    monitored.value = 'LINE-16-22'; monitored.dispatchEvent(new Event('change'));
    await sensitivity(); choice('sensitivity'); await send();
    const explicit = (await api('state')).hits.at(-1).body.analysis_context;
    check('explicit monitored selection stays explicit', explicit.monitored_line_id === 'LINE-16-22'
      && refState().receipts.sensitivity.receipt.monitored_selection === 'explicit');
    choice('security'); await send();
    check('Security sends null even with an explicit monitored selection on screen', (await api('state')).hits.at(-1).body.analysis_context.monitored_line_id === null);
    await security('LINE-1-2'); await sensitivity(); choice('sensitivity');
    const unevaluatedImage = analysisImage(), unevaluatedCounts = (await api('state')).counts;
    await send();
    check('real NO_OVERLOAD is explained as unevaluated without invented candidates', refState().receipts.sensitivity.receipt.assessment === 'not_evaluated'
      && refState().receipts.sensitivity.receipt.monitored_line_id === null && state().view.analysis.sensitivity.candidates.length === 0
      && lastAnswer().includes('NO_OVERLOAD') && analysisImage() === unevaluatedImage
      && JSON.stringify(unevaluatedCounts) === JSON.stringify((await api('state')).counts));
    const manual = (await api('state')).hits.filter(hit => hit.body === null);
    check('all manual opt-ins carry three headers and unique UUIDs', manual.every(hit => Object.keys(hit.headers).length === 3
      && UUID.test(hit.headers['x-ems-request-id'])) && new Set(manual.map(hit => hit.headers['x-ems-request-id'])).size === manual.length);
    check('sensitivity fixes top_n at five', manual.find(hit => hit.path.includes('sensitivity')).query.includes('top_n=5'));
  }

  async function gating() {
    await choose();
    check('unadvertised KPG keeps reference controls hidden', byId('kemsChatMode').closest('.kems-reference').hidden);
    await security();
    await send('기본 채팅');
    check('unadvertised manual request has no opt-in', (await api('state')).hits.filter(hit => !hit.body).every(hit => Object.keys(hit.headers).length === 0));
    check('basic body remains exact legacy contract', Object.keys((await api('state')).hits.at(-1).body).sort().join() === 'grid_id,message,session_id');
    await control({advertise: true});
    await choose('mn-osm-35k');
    check('MN ignores advertised v1 even when fixture claims it', byId('kemsChatMode').closest('.kems-reference').hidden);
    await send('몽골 기본 채팅');
    // Existing real MN line, selected from the map search by the same UI flow.
    const q = byId('kemsQ'); q.value = '751261406'; q.dispatchEvent(new Event('input'));
    const hit = [...byId('kemsRes').querySelectorAll('.msr')].find(node => node.querySelector('b')?.textContent === '선로 751261406-1_0');
    if (!hit) throw new Error('MN fixture line missing');
    hit.dispatchEvent(new MouseEvent('mousedown', {bubbles: true, cancelable: true}));
    byId('kemsCard').querySelector('.kems-an-run').click();
    await until(() => state().view.analysis.security.status === 'ready', 'MN N-1');
    await sensitivity();
    const before = (await api('state')).hits.length;
    await choose('kr-osm-transmission');
    submit('차단');
    await pause(50);
    check('Korea performs no chat or analysis', before === (await api('state')).hits.length && byId('kemsChatInput').disabled);
  }

  async function invalid_receipts() {
    await choose(); await readySecurity();
    for (const [key, value] of [['grid_id', 'mn-osm-35k'], ['kind', 'sensitivity'], ['outage_line_id', 'LINE-1-2'],
      ['monitored_line_id', 'LINE-16-22'], ['producer_request_id', '10000000-0000-4000-8000-000000000001'],
      ['session_id', 'foreign-owner'], ['model_revision', 'bad'], ['assessment', 'not_evaluated']]) {
      await control({rule: {patches: [[['analysis_receipt', key], value]]}});
      await security();
      choice();
      check('reject malformed/manual mismatched receipt ' + key, !refState().receipts.security && byId('kemsChatSend').disabled);
    }
    await security();
    await control({rule: {patches: [[['analysis_receipt', 'model_revision'], 'a'.repeat(64)]]}});
    await sensitivity(); choice('security_then_sensitivity');
    check('different model receipts cannot form chain', byId('kemsChatSend').disabled && text('kemsReferenceStatus').includes('서로 다른 모델'));
    await control({rule: {patches: [[['analysis_receipt_error'], {code: 'RESULT_NOT_REGISTERED'}]]}});
    await security(); choice();
    check('receipt plus registration error is never accepted', !refState().receipts.security && byId('kemsChatSend').disabled);
    await control({rule: {patches: [[['analysis_receipt'], null]]}});
    await security();
    check('legacy response is not assumed registered', !refState().receipts.security && state().view.analysis.security.status === 'ready');
  }

  async function invalid_envelopes() {
    await choose(); await readySecurity();
    for (const [keys, value] of [[['grid_id'], 'mn-osm-35k'], [['session_id'], 'foreign'], [['request_id'], '10000000-0000-4000-8000-000000000001'],
      [['analysis_context', 'revision'], 999], [['analysis_context', 'monitored_line_id'], 'LINE-16-22'],
      [['analysis', 'status'], 'completed'], [['analysis', 'source'], 'tool_execution'],
      [['analysis', 'results', 0, 'result_id'], '20000000-0000-4000-8000-000000000001'],
      [['analysis', 'results', 0, 'producer_request_id'], '20000000-0000-4000-8000-000000000001'],
      [['ui_updates'], [{type: 'security', result: {}}]]]) {
      if (!refState().receipts.security) await security();
      choice();
      const overlay = JSON.stringify(window.kemsConsole.map.overlay()), side = text('kemsSide'), answers = countAnswers();
      await control({rule: {patches: [[keys, value]]}});
      await send();
      check('reject invalid envelope ' + keys.join('.'), countAnswers() === answers && byId('kemsChatStatus').classList.contains('kems-err')
        && overlay === JSON.stringify(window.kemsConsole.map.overlay()) && side === text('kemsSide'));
    }
  }

  function hold(match, failure = false) {
    let release, seen;
    const arrived = new Promise(resolve => { seen = resolve; });
    window.fetch = async (url, options) => {
      if (!match(String(url))) return nativeFetch(url, options);
      // Execute the real route but ignore Abort while delaying delivery of its actual response.
      const response = await nativeFetch(url, {...options, signal: undefined});
      const body = await response.text();
      return new Promise((resolve, reject) => {
        release = () => failure ? reject(new TypeError('explicit delayed transport failure'))
          : resolve(new Response(body, {status: response.status, headers: {'Content-Type': 'application/json'}}));
        seen();
      });
    };
    return {arrived, release: () => release(), restore: () => { window.fetch = nativeFetch; }};
  }

  async function races() {
    await choose(); await readySecurity();
    const changes = {
      outage: () => security('LINE-58-59'),
      monitored: async () => { const select = document.querySelector('.kems-an-mon'); select.value = 'LINE-16-22'; select.dispatchEvent(new Event('change')); },
      reload: () => choose(),
      clear: async () => document.querySelector('.kems-an-clear').click(),
      reset: async () => byId('kemsChatReset').click(),
      roundtrip: async () => { await choose('mn-osm-35k'); await choose(); },
      mode: async () => choice('security', 'basic'),
      operation: async () => choice('sensitivity'),
    };
    for (const [name, change] of Object.entries(changes)) {
      for (const failure of [false, true]) {
        await security(); await sensitivity(); choice();
        const held = hold(url => url.endsWith('/chat'), failure);
        try {
          submit(); await held.arrived;
          await change();
          const messages = text('kemsChatMessages'), image = analysisImage();
          held.release(); await pause(60);
          check(`${name}: late ${failure ? 'failure' : 'success'} ignoring Abort discarded`, messages === text('kemsChatMessages')
            && image === analysisImage() && byId('kemsChat').dataset.busy === 'false');
        } finally { held.restore(); }
      }
    }
    await security(); choice();
    const held = hold(url => url.endsWith('/chat'));
    try {
      submit(); await held.arrived;
      const revision = refState().revision;
      selectLine('LINE-1-2');
      input('작성 중 초안');
      const svg = byId('kemsMapHost').querySelector('svg');
      svg.dispatchEvent(new WheelEvent('wheel', {deltaY: -100, clientX: 500, clientY: 400, bubbles: true, cancelable: true}));
      check('map selection, zoom and draft keep analysis context', refState().revision === revision && refState().selectedOutageLineId === 'LINE-16-28');
      held.release(); await until(() => byId('kemsChat').dataset.busy === 'false', 'valid held reference');
      check('valid held response keeps draft and receipt', lastAnswer().includes('기존 결과 설명') && byId('kemsChatInput').value === '작성 중 초안'
        && refState().receipts.security !== null);
    } finally { held.restore(); }
  }

  async function lifetime() {
    await choose(); await readySecurity(); choice();
    const counts = (await api('state')).counts;
    await control({expire: true}); await send();
    check('server expiry reports failure without recalculation', text('kemsChatStatus').includes('만료')
      && JSON.stringify(counts) === JSON.stringify((await api('state')).counts));
    await security(); choice();
    await control({change_model: true});
    const changedCounts = (await api('state')).counts;
    await send();
    check('model change rejects reference without recalculation', text('kemsChatStatus').includes('모델 상태가 변경')
      && JSON.stringify(changedCounts) === JSON.stringify((await api('state')).counts));
    await readySecurity();
    await control({short_ttl: true}); await security(); choice();
    await until(() => byId('kemsChatSend').disabled, 'local expiry', 3000);
    const hits = (await api('state')).hits.length;
    submit(); await pause(50);
    check('expired button and programmatic submit cannot send', text('kemsReferenceStatus').includes('만료') && (await api('state')).hits.length === hits);
  }

  async function errors() {
    await choose(); await readySecurity();
    for (const code of ['ANALYSIS_REFERENCE_UNAVAILABLE', 'ANALYSIS_REFERENCE_STALE', 'REQUEST_REPLAY_STALE', 'REQUEST_IN_PROGRESS',
      'REQUEST_ID_REUSE', 'SESSION_BUSY', 'ANALYSIS_BUSY', 'MODEL_IDENTITY_UNAVAILABLE', 'ANALYSIS_CONTEXT_UNSUPPORTED']) {
      if (!refState().receipts.security) await security();
      choice();
      const composer = byId('kemsChatInput'), count = (await api('state')).hits.length;
      await control({rule: {status: 409, replace: {detail: {code, message: 'sensitive provider text must not show', request_id: null}}}});
      await send();
      check('fixed structured error ' + code, byId('kemsChatStatus').classList.contains('kems-err') && composer === byId('kemsChatInput')
        && !text('kemsChatStatus').includes('sensitive') && (await api('state')).hits.length === count + 1);
    }
    await security(); choice();
    await control({rule: {status: 409, replace: {detail: 'SESSION_BUSY'}}}); await send();
    check('legacy string busy handled', text('kemsChatStatus').includes('다른 요청'));
    await security(); choice();
    const realTimer = window.setTimeout;
    window.setTimeout = (fn, ms, ...args) => realTimer(fn, ms === 60000 ? 100 : ms, ...args);
    const held = hold(url => url.endsWith('/chat'));
    try {
      submit(); await held.arrived;
      await until(() => byId('kemsChat').dataset.busy === 'false', 'reference timeout', 3000);
      const messages = text('kemsChatMessages');
      held.release(); await pause(50);
      check('timeout retires session and late success stays discarded', text('kemsChatStatus').includes('시간이 초과')
        && !refState().receipts.security && messages === text('kemsChatMessages'));
    } finally { held.restore(); window.setTimeout = realTimer; }
  }

  async function pending_manual() {
    await choose(); await readySecurity(); choice();
    const held = hold(url => url.includes('sensitivity-analysis'));
    try {
      document.querySelector('#kemsSide [data-action="sensitivity"]').click();
      await held.arrived;
      check('manual loading disables reference', byId('kemsChatSend').disabled);
      byId('kemsChatReset').click();
      held.release();
      await until(() => state().view.analysis.sensitivity.status === 'ready', 'old session manual completion');
      check('reset preserves manual VM but discards old session receipt', !refState().receipts.sensitivity && !refState().receipts.security);
    } finally { held.restore(); }
    await security();
    const firstOwner = refState().receipts.security.sessionId;
    await sensitivity();
    check('fresh sequential analysis restores common owner', refState().receipts.sensitivity.sessionId === firstOwner);
    choice('security_then_sensitivity'); await send();
    check('fresh chain can be referenced after reset', lastAnswer().includes('기존 결과 설명'));
    const old = analysisImage(); byId('kemsChatReset').click();
    check('reset never removes manual analysis or replays overlays', state().view.analysis.security.status === 'ready'
      && state().view.analysis.sensitivity.status === 'ready' && !refState().receipts.security
      && JSON.parse(old).overlay !== null && byId('kemsChatSend').disabled);
  }

  /* Review F1: a pending explanation locks the mode/operation selectors until it settles. */
  async function selection_lock() {
    await choose(); await readySecurity(); await sensitivity();
    const owner = refState().receipts.security.sessionId, receipts = JSON.stringify(refState().receipts);
    for (const [operation, choiceMode] of [['sensitivity', 'reference'], ['security_then_sensitivity', 'reference'],
      ['security', 'basic'], ['security_then_sensitivity', 'reference']]) choice(operation, choiceMode);
    check('selection alone keeps receipts while nothing is pending', JSON.stringify(refState().receipts) === receipts
      && !byId('kemsChatMode').disabled && !byId('kemsChatOperation').disabled && !byId('kemsChatSend').disabled);
    await send();
    const used = (await api('state')).hits.at(-1).body;
    check('the same session and receipts still explain after selection changes', lastAnswer().includes('기존 결과 설명 · 새 계산 없음')
      && used.session_id === owner && JSON.stringify(Object.values(used.analysis_context.references))
        === JSON.stringify([refState().receipts.security.receipt.result_id, refState().receipts.sensitivity.receipt.result_id]));

    choice('security', 'basic');
    const basic = hold(url => url.endsWith('/chat'));
    try {
      const answers = countAnswers();
      submit('기본 채팅 대기'); await basic.arrived;
      check('basic chat does not lock the selectors', !byId('kemsChatMode').disabled && !byId('kemsChatOperation').disabled);
      choice('security', 'reference');
      basic.release(); await until(() => byId('kemsChat').dataset.busy === 'false', 'basic chat settled');
      check('mode change during basic chat keeps that chat and its receipts', countAnswers() === answers + 1
        && lastAnswer().includes('고정 기본 채팅 응답') && JSON.stringify(refState().receipts) === receipts);
    } finally { basic.restore(); }

    for (const outcome of ['success', 'failure', 'timeout']) {
      if (!refState().receipts.security || !refState().receipts.sensitivity) { await security(); await sensitivity(); }
      choice('security_then_sensitivity');
      const before = JSON.stringify(refState()), answers = countAnswers();
      const realTimer = window.setTimeout;
      if (outcome === 'timeout') window.setTimeout = (fn, ms, ...args) => realTimer(fn, ms === 60000 ? 100 : ms, ...args);
      const held = hold(url => url.endsWith('/chat'), outcome === 'failure');
      try {
        submit();
        const locked = byId('kemsChatMode').disabled && byId('kemsChatOperation').disabled && byId('kemsChat').dataset.busy === 'true';
        selectLine('LINE-1-2'); input('대기 중 초안');
        check(`${outcome}: selectors locked; map selection and draft keep the pending context`, locked && JSON.stringify(refState()) === before);
        await held.arrived;
        if (outcome !== 'timeout') held.release();
        await until(() => byId('kemsChat').dataset.busy === 'false', outcome + ' settled', 5000);
        const settled = text('kemsChatMessages');
        if (outcome === 'timeout') { held.release(); await pause(60); }
        check(`${outcome}: selectors enabled again, draft kept, nothing late applied`, !byId('kemsChatMode').disabled
          && !byId('kemsChatOperation').disabled && byId('kemsChatInput').value === '대기 중 초안' && settled === text('kemsChatMessages'));
        check(`${outcome}: the unchanged request settles as before`, outcome === 'success'
          ? countAnswers() === answers + 1 && lastAnswer().includes('기존 결과 설명') && JSON.stringify(refState()) === before
          : countAnswers() === answers && byId('kemsChatStatus').classList.contains('kems-err') && !refState().receipts.security
            && text('kemsChatStatus').includes(outcome === 'timeout' ? '시간이 초과' : '연결할 수 없습니다'));
      } finally { held.restore(); window.setTimeout = realTimer; }
    }

    await security(); await sensitivity(); choice('security');
    const held = hold(url => url.endsWith('/chat'));
    try {
      submit(); await held.arrived;
      byId('kemsChatReset').click();
      const afterReset = text('kemsChatMessages');
      check('reset still cancels a locked reference and unlocks the selectors', byId('kemsChat').dataset.busy === 'false'
        && !byId('kemsChatMode').disabled && !byId('kemsChatOperation').disabled && !refState().receipts.security);
      held.release(); await pause(60);
      check('late answer after reset stays discarded', afterReset === text('kemsChatMessages'));
    } finally { held.restore(); }
  }

  /* Review F3: a receipt that expires while its explanation is in flight is rejected and named as expiry. */
  async function expiry_in_flight() {
    await choose(); await readySecurity();
    async function heldReference(patch) {
      await control({ttl: 3});  // real server TTL; the browser waits on the real clock
      await security(); choice('security');
      const expiry = Date.parse(refState().receipts.security.receipt.expires_at);
      const ready = !byId('kemsChatSend').disabled && expiry > Date.now();
      const answers = countAnswers(), overlay = JSON.stringify(window.kemsConsole.map.overlay()), side = text('kemsSide');
      if (patch) await control({rule: {patches: [patch]}});
      const held = hold(url => url.endsWith('/chat'));
      try {
        submit(); await held.arrived;
        const served = (await api('state')).hits.at(-1);
        await until(() => Date.now() > expiry + 50, 'receipt expiry', 8000);
        held.release();
        await until(() => byId('kemsChat').dataset.busy === 'false', 'expired reference settled');
        return {ready, served, rejected: countAnswers() === answers && byId('kemsChatStatus').classList.contains('kems-err')
          && overlay === JSON.stringify(window.kemsConsole.map.overlay()) && side === text('kemsSide')};
      } finally { held.restore(); }
    }
    const expired = await heldReference(null);
    check('server explained the receipt while it was still valid', expired.ready && expired.served.status === 200
      && expired.served.response.analysis.status === 'referenced');
    check('receipt expired in flight is rejected and not reusable', expired.rejected && !refState().receipts.security
      && byId('kemsChatSend').disabled);
    check('in-flight expiry is reported as expiry, not as a contract violation', text('kemsChatStatus').includes('분석 결과가 만료되었습니다')
      && !text('kemsChatStatus').includes('계약'));
    const tampered = await heldReference([['analysis', 'results', 0, 'result_id'], '20000000-0000-4000-8000-000000000001']);
    check('a tampered response stays a contract violation even after expiry', tampered.ready && tampered.rejected
      && text('kemsChatStatus').includes('계약') && !text('kemsChatStatus').includes('만료되었습니다'));
    await control({ttl: 600});
    await security(); choice('security'); await send();
    check('an unexpired reference is still accepted', lastAnswer().includes('기존 결과 설명 · 새 계산 없음')
      && !byId('kemsChatStatus').classList.contains('kems-err'));
  }

  try {
    await window.kemsConsole.ready;
    await ({receipts, gating, invalid_receipts, invalid_envelopes, races, lifetime, errors, pending_manual,
      selection_lock, expiry_in_flight})[mode]();
  } catch (error) {
    check('probe exception', false, String(error.stack || error));
  } finally {
    await control({report: {case: mode, results, errors: window.__referenceErrors}});
  }
})();
