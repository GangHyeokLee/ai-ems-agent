// Chat uses real domain-tool fixtures and keeps panel state; manual actions still expand it.
(async () => {
  const results = [], cases = [];
  const check = (name, pass, detail) => results.push({name, pass: !!pass, detail: detail ?? null});
  const $ = id => document.getElementById(id);
  const count = selector => document.querySelectorAll(selector).length;
  const tick = () => fetch('/__probe__/tick').then(r => r.json());
  const wait = async predicate => {
    const start = performance.now();
    while (!predicate()) { if (performance.now() - start > 90000) throw Error('timeout'); await tick(); }
  };
  const collapsed = id => $(id).classList.contains('collapsed');
  const userSet = (id, value) => { if (collapsed(id) !== value) $(id).querySelector('.analysis-box-header').click(); };
  const sec = 'security-analysis-box', gen = 'generator-contingency-box';
  const send = async key => { $('chat-message').value = `[fixture:${key}]`; await sendChatMessage(); };
  const switchGrid = async id => {
    $('grid-select').value = id; $('grid-select').dispatchEvent(new Event('change'));
    await wait(() => selectedGridId === id && currentMapData && pfPreview);
  };
  const gate = async path => {
    const {id} = await fetch('/__probe__/gates', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({match:path})}).then(r => r.json());
    return {requested:fetch(`/__probe__/gates/${id}?wait=1`).then(r => r.json()), release:() => fetch(`/__probe__/gates/${id}/release`, {method:'POST'})};
  };
  try {
    await wait(() => currentMapData && pfPreview);
    for (const grid of ['kr-kpg193', 'mn-osm-35k']) {
      if (selectedGridId !== grid) await switchGrid(grid);
      const mn = grid === 'mn-osm-35k';
      const scenarios = mn ? [['security', 'mn_security'], ['sensitivity', 'mn_followup'], ['chain', 'mn_chain']]
        : [['security', 'security'], ['sensitivity', 'sensitivity'], ['generator', 'generator']];
      for (const state of ['collapsed', 'expanded']) {
        for (const [kind, fixture] of scenarios) {
          const closed = state === 'collapsed', box = kind === 'generator' ? gen : sec;
          userSet(sec, closed); userSet(gen, closed);
          const calls = [], originalSet = setAnalysisBoxCollapsed;
          setAnalysisBoxCollapsed = (...args) => { calls.push(args); return originalSet(...args); };
          try { await send(fixture); } finally { setAnalysisBoxCollapsed = originalSet; }
          const label = `${grid}:${kind}:${state}`;
          cases.push(label);
          check(`${label}: no automatic panel state calls`, calls.length === 0, calls);
          check(`${label}: both panels keep user state`, collapsed(sec) === closed && collapsed(gen) === closed);
          check(`${label}: ARIA keeps user state`, $(box).querySelector('.analysis-toggle').getAttribute('aria-expanded') === String(!closed));
          const target = kind === 'generator' ? 'generator-contingency-result' : kind === 'security' ? 'security-result' : 'sensitivity-result';
          check(`${label}: newest results rendered while hidden or visible`, kind === 'generator'
            ? $(target).textContent.includes('GEN-124#1') && $(target).textContent.includes('Post LF')
            : $(target).querySelector('.result-card')?.dataset.gridId === grid);
          check(`${label}: map overlay updated`, kind === 'generator' ? count('.generator-outage-marker') === 1
            : kind === 'security' ? count('.outage-line') > 0 : count('.candidate-marker') > 0 && count('.monitored-line') > 0);
          if (closed) {
            check(`${label}: panel content stays hidden`, getComputedStyle($(box).querySelector('.analysis-box-content')).display === 'none');
            userSet(box, false);
            check(`${label}: manual expansion reveals latest result`, getComputedStyle($(box).querySelector('.analysis-box-content')).display !== 'none'
              && $(target).textContent.includes(kind === 'generator' ? 'GEN-124#1' : kind === 'security' ? 'CONVERGED' : 'Top Candidates: 5'));
          }
        }
      }
      // Real manual panel requests and map selection retain their expansion behavior.
      const line = mn ? '751261406-1_0' : 'LINE-16-28';
      userSet(sec, true);
      document.querySelector(`.network-line-hit[data-line-id="${line}"]`).dispatchEvent(new MouseEvent('click', {bubbles:true}));
      check(`${grid}: map line selection expands Security`, !collapsed(sec) && $('outage-line').value === line);
      $('analyze-button').click(); userSet(sec, true);
      await wait(() => latestSecurityResult?.outage_line_id === line && $('security-result').querySelector('.result-card'));
      check(`${grid}: manual Analyze response still expands Security`, !collapsed(sec));
      $('sensitivity-button').click(); userSet(sec, true);
      await wait(() => $('sensitivity-result').querySelector('.result-card'));
      check(`${grid}: manual Sensitivity response still expands Security`, !collapsed(sec) && count('.candidate-marker') > 0);
      if (!mn) {
        userSet(gen, true); selectGenerator('GEN-124#1', 124);
        check('KPG: map generator selection still expands its panel', !collapsed(gen));
        $('generator-analyze-button').click(); userSet(gen, true);
        await wait(() => !$('generator-analyze-button').disabled && $('generator-contingency-result').textContent.includes('Post LF'));
        check('KPG: manual Generator Analyze response still expands its panel', !collapsed(gen));
      }
      // Respect a user's change while a response is in flight, rather than restoring a snapshot.
      userSet(sec, !mn); userSet(gen, !mn);
      const held = await gate(mn ? '/api/grids/mn-osm-35k/chat' : '/api/chat');
      const pending = send(mn ? 'mn_chain' : 'generator');
      await held.requested;
      userSet(sec, mn); userSet(gen, mn);
      await held.release(); await pending;
      check(`${grid}: pending chat keeps the latest user panel choice`, collapsed(sec) === mn && collapsed(gen) === mn);
    }
    check('Chat panel probe completed without agent errors', !$('chat-log').textContent.includes('Agent 오류:'));
  } catch (error) { check('probe exception', false, error.stack || String(error)); }
  const pre = document.createElement('pre'); pre.id = '__probe_result'; pre.textContent = JSON.stringify({results, cases}); document.body.appendChild(pre);
})();
