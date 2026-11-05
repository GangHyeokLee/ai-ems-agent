/* MAINT-03: observe production fetches; hold/fault only named test responses. */
(async () => {
  'use strict';
  const results = [], calls = [], gates = [];
  const check = (name, pass, detail = null) => results.push({name, pass: !!pass, detail});
  const nativeFetch = window.fetch.bind(window), nativeTimer = window.setTimeout.bind(window);
  const pause = (ms) => new Promise((resolve) => nativeTimer(resolve, ms));
  const view = () => window.kemsConsole.state().view;
  const choose = (grid) => {
    const select = document.getElementById('model');
    select.value = 'api:' + grid;
    select.dispatchEvent(new Event('change'));
  };
  const KPG = 'kr-kpg193', MN = 'mn-osm-35k';
  const map = (grid) => `/api/grids/${grid}/map`, pf = (grid) => `/api/grids/${grid}/pf-preview`;
  let sequence = 0, ignoreAbort = null, hang = null, shortTimers = false;
  window.setTimeout = (fn, ms, ...args) => nativeTimer(fn, shortTimers && ms === (hang === map(KPG) ? 30000 : 20000) ? 80 : ms, ...args);
  window.fetch = async (input, options = {}) => {
    const path = new URL(String(input), location.href).pathname;
    if (!/\/(map|pf-preview)$/.test(path)) return nativeFetch(input, options);
    const call = {path, start: ++sequence, mapStatus: view()?.map.status, aborted: false};
    calls.push(call);
    if (options.signal) options.signal.addEventListener('abort', () => { call.aborted = true; });
    if (path === hang) {
      return new Promise((resolve, reject) => options.signal.addEventListener('abort', () => {
        call.end = ++sequence;
        reject(new DOMException('Timeout fixture', 'AbortError'));
      }, {once: true}));
    }
    const ignore = path === ignoreAbort;
    if (ignore) { ignoreAbort = null; call.ignoredAbort = true; }
    try {
      const response = await nativeFetch(input, ignore ? {...options, signal: undefined} : options);
      const read = response.text.bind(response);
      response.text = async () => {
        const text = await read();
        call.end = ++sequence;
        return text;
      };
      return response;
    } catch (error) {
      call.end = ++sequence;
      throw error;
    }
  };
  const get = async (path) => (await nativeFetch(path, {cache: 'no-store'})).json();
  const post = (path, body = {}) => nativeFetch(path, {method: 'POST', body: JSON.stringify(body)});
  async function until(predicate, name) {
    const end = performance.now() + 15000;
    while (performance.now() < end) {
      if (await predicate()) return;
      await pause(10);
    }
    throw new Error('Timed out: ' + name);
  }
  const ready = (grid) => until(() => view()?.gridId === grid && view().map.status === 'ready' && view().pf.status === 'ready', grid + ' ready');
  async function gate(path, fail = false) {
    const id = (await (await post('/__probe__/gates', {match: '=' + path, fail})).json()).id;
    gates.push(id);
    return id;
  }
  // Wait for the real upstream calculation/body to finish; only delivery is held.
  // This does not serialize routes or bypass the production network guard.
  const requested = (id) => until(async () => (await get(`/__probe__/gates/${id}/response-ready`)).ready, 'response held');
  const release = (id) => post(`/__probe__/gates/${id}/release`);
  const count = (path, offset = 0) => calls.slice(offset).filter((call) => call.path === path);
  async function rule(spec) { await post('/__probe__/rules/clear'); await post('/__probe__/rules', spec); }
  const caseName = new URLSearchParams(location.search).get('loading_case');
  try {
    await window.kemsConsole.ready;
    if (caseName === 'order') {
      const mapGate = await gate(map(KPG)), pfGate = await gate(pf(KPG));
      choose(KPG);
      await requested(mapGate);
      check('KPG: PF not started while map response held', count(pf(KPG)).length === 0 && view().map.status === 'loading', calls);
      await release(mapGate);
      await requested(pfGate);
      const mapCall = count(map(KPG))[0], pfCall = count(pf(KPG))[0];
      check('KPG: map body read before PF starts', mapCall.end < pfCall.start, calls);
      check('KPG: map displayed while PF is still pending', pfCall.mapStatus === 'ready' && view().map.status === 'ready' && view().pf.status === 'loading' && window.kemsConsole.map.counts().buses === 193, view());
      await release(pfGate);
      await ready(KPG);
      check('KPG: one map and one PF without retries', count(map(KPG)).length === 1 && count(pf(KPG)).length === 1);
      const mnMap = await gate(map(MN)), mnPf = await gate(pf(MN));
      choose(MN);
      await requested(mnMap);
      await requested(mnPf);
      check('MN: PF remains parallel while map response held', !count(map(MN))[0].end && count(pf(MN)).length === 1, calls);
      await release(mnMap);
      await release(mnPf);
      await ready(MN);
      check('MN: real map and PF remain usable', window.kemsConsole.map.counts().buses === 173 && view().pf.status === 'ready');
    } else if (caseName === 'failures') {
      for (const spec of [{status: 500}, {merge: {grid_id: MN}}]) {
        const offset = calls.length;
        await rule({match: '=' + map(KPG), ...spec});
        choose(KPG);
        await until(() => view()?.map.status === 'error', 'map failure');
        check('map failure blocks PF and preserves error', view().pf.status === 'blocked' && count(pf(KPG), offset).length === 0 && window.kemsConsole.map.counts() === null, view());
      }
      const offset = calls.length;
      await rule({match: '=' + pf(KPG), status: 409, detail: 'ANALYSIS_BUSY'});
      choose(KPG);
      await until(() => view()?.pf.status === 'error', 'PF busy');
      await pause(100);
      check('PF 409 remains visible without retry', view().map.status === 'ready' && view().pf.error.includes('409') && count(pf(KPG), offset).length === 1, view());
      await post('/__probe__/rules/clear');
      choose(KPG);
      await ready(KPG);
      check('explicit reload recovers after failures', view().map.status === 'ready' && view().pf.status === 'ready');
    } else if (caseName === 'cancel') {
      for (const endpoint of ['map', 'pf']) for (const fail of [false, true]) {
        const path = endpoint === 'map' ? map(KPG) : pf(KPG);
        const offset = calls.length, held = await gate(path, fail);
        ignoreAbort = path;
        choose(KPG);
        await requested(held);
        const oldCall = count(path, offset)[0];
        if (endpoint === 'map') { choose(MN); await ready(MN); }
        else { choose(KPG); await ready(KPG); }
        const before = JSON.stringify(view());
        await release(held);
        await until(() => !!oldCall.end, 'old response body consumed');
        await pause(40);
        check(`${endpoint} late ${fail ? 'failure' : 'success'} ignored after ${endpoint === 'map' ? 'grid switch' : 'same-grid reload'}`, oldCall.aborted && oldCall.ignoredAbort && JSON.stringify(view()) === before, {oldCall, current: view()});
        if (endpoint === 'map') check('retired KPG map never starts queued PF', count(pf(KPG), offset).length === 0, calls.slice(offset));
      }
      // With normal Abort support, a retired map rejects and still cannot start PF.
      const offset = calls.length, held = await gate(map(KPG));
      choose(KPG);
      await requested(held);
      choose(MN);
      await ready(MN);
      await release(held);
      await pause(50);
      check('normal Abort cancels KPG load without queued PF', count(map(KPG), offset)[0].aborted && count(pf(KPG), offset).length === 0 && view().gridId === MN);
    } else if (caseName === 'timeout') {
      for (const endpoint of ['map', 'pf']) {
        const offset = calls.length;
        hang = endpoint === 'map' ? map(KPG) : pf(KPG);
        shortTimers = true;
        choose(KPG);
        await until(() => view()?.[endpoint].status === 'error', endpoint + ' timeout');
        shortTimers = false;
        hang = null;
        check(`${endpoint} timeout aborts and remains visible`, view()[endpoint].error.includes('응답 시간 초과') && count(endpoint === 'map' ? map(KPG) : pf(KPG), offset)[0].aborted, view());
        check(`${endpoint} timeout keeps map/PF contract`, endpoint === 'map' ? view().pf.status === 'blocked' && count(pf(KPG), offset).length === 0 : view().map.status === 'ready', view());
      }
      choose(KPG);
      await ready(KPG);
      check('explicit reload recovers after timeout', view().pf.status === 'ready');
    } else throw new Error('Unknown loading case');
  } catch (error) {
    check('probe completed', false, String(error.stack || error));
  } finally {
    for (const id of gates) await release(id);
    const pre = document.createElement('pre');
    pre.id = '__probe_result';
    pre.hidden = true;
    pre.textContent = JSON.stringify({case: caseName, results, calls, errors: window.__probeErrors || []});
    document.body.append(pre);
    await post('/__probe__/release-load');
  }
})();
