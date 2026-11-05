// Real HTTP chat handlers, server sessions, LangGraph/Mongolia tools and KPG tools.
// Only model choices are scripted by chat_session_app.py; no precomputed chat responses.
(async () => {
  const reportKey = '__gridChatRestoreProbe';
  const reloaded = new URLSearchParams(location.search).get('phase') === 'reloaded';
  const saved = reloaded ? JSON.parse(sessionStorage.getItem(reportKey)) : null;
  const results = saved?.results || [];
  const check = (name, pass, detail) => results.push({name, pass: !!pass, detail: detail ?? null});
  const $ = id => document.getElementById(id);
  const count = selector => document.querySelectorAll(selector).length;
  const wait = async predicate => {
    const start = performance.now();
    while (!predicate()) {
      if (performance.now() - start > 90000) throw Error('timeout');
      await fetch('/__probe__/tick');
    }
  };
  const history = () => $('chat-log').innerHTML;
  const analysis = () => JSON.stringify([
    $('security-result').innerHTML, $('sensitivity-result').innerHTML,
    $('outage-line').value, $('monitored-line').value,
    [...document.querySelectorAll('.analysis-marker, .outage-line, .violation-line, .monitored-line')].map(el => el.outerHTML),
  ]);
  const switchGrid = async id => {
    $('grid-select').value = id; $('grid-select').dispatchEvent(new Event('change'));
    await wait(() => selectedGridId === id && currentMapData && pfPreview);
  };
  const messages = {
    kpg_security: 'LINE-16-28 선로 N-1 분석해 줘 [session:kpg_security]',
    kpg_followup: '이어서 같은 사고의 민감도 후보 5개 알려줘 [session:kpg_followup]',
    mn_security: '751261406-1_0 선로 N-1 분석해 줘 [session:mn_security]',
    mn_followup: '이어서 민감도 후보 5개 알려줘 [session:mn_followup]',
  };
  const send = async key => { $('chat-message').value = messages[key]; await sendChatMessage(); };
  const gate = async (match, fail=false) => {
    const {id} = await fetch('/__probe__/gates', {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({match, fail}),
    }).then(r => r.json());
    return {
      requested: fetch(`/__probe__/gates/${id}?wait=1`).then(r => r.json()),
      release: () => fetch(`/__probe__/gates/${id}/release`, {method:'POST'}),
    };
  };
  const finish = () => {
    const pre = document.createElement('pre'); pre.id = '__probe_result';
    pre.textContent = JSON.stringify({results}); document.body.appendChild(pre);
  };
  try {
    await wait(() => currentMapData && pfPreview);
    if (reloaded) {
      check('Reload clears displayed messages', count('#chat-log .chat-message') === 0);
      check('Reload creates fresh sessions for both grids', getSessionId('kr-kpg193') !== saved.krSession && getSessionId('mn-osm-35k') !== saved.mnSession);
      check('Legacy shared sessionStorage ID is never reused', getSessionId() !== sessionStorage.getItem('ai-ems-agent-session-id'));
      sessionStorage.removeItem(reportKey);
      sessionStorage.removeItem('ai-ems-agent-session-id');
      return finish();
    }
    const calls = [], originalFetch = window.fetch;
    window.fetch = (url, options) => {
      if (String(url).endsWith('/chat')) calls.push({url:String(url), ...JSON.parse(options.body)});
      return originalFetch(url, options);
    };
    sessionStorage.setItem('ai-ems-agent-session-id', 'legacy-shared-session');
    const krSession = getSessionId();
    await send('kpg_security');
    const krHistory = history();
    check('KPG initial chat uses real Security tool', latestSecurityResult?.outage_line_id === 'LINE-16-28' && count('#chat-log .chat-message.agent') === 1);
    await switchGrid('mn-osm-35k');
    const mnSession = getSessionId();
    check('New grid starts with its own empty history and session', count('#chat-log .chat-message') === 0 && mnSession !== krSession);
    check('Full grid ID separates future grids in the same country', getSessionId('mn-another-grid') !== mnSession && getSessionId('kr-another-grid') !== krSession);
    await send('mn_security');
    const mnHistory = history();
    check('MN initial chat uses real Security tool', latestSecurityResult?.outage_line_id === '751261406-1_0' && count('#chat-log .chat-message.agent') === 1);
    await switchGrid('kr-kpg193');
    check('KPG history order and Markdown are restored', history() === krHistory && getSessionId() === krSession);
    check('Restoring KPG chat does not reapply old analysis', count('.result-card, .analysis-marker, .outage-line') === 0);
    check('KPG help and placeholder follow selected grid', !$('chat-message').placeholder.includes('합성 PF') && $('assistant-help').textContent.includes('KPG'));
    await send('kpg_followup');
    check('KPG followup resolves previous outage from real server history', count('#sensitivity-result .result-item') === 5 && $('outage-line').value === 'LINE-16-28' && !$('chat-log').textContent.includes('Agent 오류:'));
    const krFollowupHistory = history();
    await switchGrid('mn-osm-35k');
    check('MN history order and original session are restored', history() === mnHistory && getSessionId() === mnSession);
    check('Restoring MN chat clears KPG cards and overlays', count('.result-card, .analysis-marker, .outage-line') === 0);
    check('MN help and placeholder follow selected grid', $('chat-message').placeholder.includes('후속 민감도') && $('assistant-help').textContent.includes('몽골'));
    setAnalysisBoxCollapsed('security-analysis-box', true);
    await send('mn_followup');
    check('MN followup resolves previous actual outage after round trip', count('#sensitivity-result .result-item') === 5 && $('outage-line').value === '751261406-1_0' && !$('chat-log').textContent.includes('Agent 오류:'));
    check('MN followup updates real sensitivity map without expanding panel', count('.candidate-marker') > 0 && count('.monitored-line') > 0 && $('security-analysis-box').classList.contains('collapsed'));
    const mnFollowupHistory = history();
    for (let i = 0; i < 3; i++) {
      await switchGrid('kr-kpg193');
      if (history() !== krFollowupHistory || getSessionId() !== krSession) throw Error('KPG history changed on repeat');
      await switchGrid('mn-osm-35k');
      if (history() !== mnFollowupHistory || getSessionId() !== mnSession) throw Error('MN history changed on repeat');
    }
    check('Repeated switching preserves every message exactly once', history() === mnFollowupHistory && count('#chat-log .chat-message') === 4);

    const background = await gate('/api/grids/mn-osm-35k/chat');
    const oldMn = send('mn_security'); await background.requested;
    await switchGrid('kr-kpg193');
    check('KPG can chat while MN request is pending', !$('chat-send').disabled && !$('chat-message').disabled);
    await send('kpg_security');
    const krActiveHistory = history(), krActiveAnalysis = analysis();
    await background.release(); await oldMn;
    check('Background MN success cannot change KPG chat', history() === krActiveHistory);
    check('Background MN success cannot change KPG cards map or inputs', analysis() === krActiveAnalysis);
    await switchGrid('mn-osm-35k');
    check('Background success restores to the correct MN conversation', count('#chat-log .chat-message') === 6 && count('#chat-log .chat-message.agent') === 3 && getSessionId() === mnSession);
    check('Background analysis is not replayed when returning', count('.result-card, .analysis-marker, .outage-line') === 0);
    await send('mn_followup');
    check('Background completion keeps actual server context usable', count('#sensitivity-result .result-item') === 5 && $('outage-line').value === '751261406-1_0');

    const returning = await gate('/api/grids/mn-osm-35k/chat');
    const beforeReturn = getChatState().messages.length;
    const pendingMn = send('mn_security'); await returning.requested;
    const callCount = calls.length;
    await switchGrid('kr-kpg193'); await switchGrid('mn-osm-35k');
    check('Returning to pending MN disables both send and input', $('chat-send').disabled && $('chat-message').disabled);
    check('Returning to pending MN restores the user message once', count('#chat-log .chat-message') === beforeReturn + 1);
    await send('mn_security');
    check('Duplicate submission cannot send a concurrent request for same session', calls.length === callCount && getChatState().messages.length === beforeReturn + 1);
    $('chat-message').value = '';
    await returning.release(); await pendingMn;
    check('Returning before completion receives exactly one answer', count('#chat-log .chat-message') === beforeReturn + 2 && getChatState().messages.length === beforeReturn + 2);
    check('Returning completion unlocks both controls', !$('chat-send').disabled && !$('chat-message').disabled);
    check('Changed view token still blocks late analysis on original grid', count('.result-card, .analysis-marker, .outage-line') === 0);
    await send('mn_followup');
    check('Followup after returning uses preserved server context', count('#sensitivity-result .result-item') === 5 && $('security-analysis-box').classList.contains('collapsed'));

    const failing = await gate('/api/grids/mn-osm-35k/chat', true);
    const pendingFail = send('mn_security'); await failing.requested;
    await switchGrid('kr-kpg193');
    const krBeforeError = history(), krBeforeErrorAnalysis = analysis();
    await failing.release(); await pendingFail;
    check('Background failure cannot change active KPG conversation or analysis', history() === krBeforeError && analysis() === krBeforeErrorAnalysis);
    await switchGrid('mn-osm-35k');
    check('Background error is retained only in MN history', $('chat-log').lastElementChild.classList.contains('system') && $('chat-log').lastElementChild.textContent.includes('Agent 오류:') && getSessionId() === mnSession);
    check('Failed request releases MN pending state', !$('chat-send').disabled && !$('chat-message').disabled);
    check('No tool evidence box returns', ![...document.querySelectorAll('#chat-log .chat-message')].some(el => el.textContent.startsWith('근거:')));
    check('All real HTTP requests reuse only their own grid session', calls.every(c => c.session_id === (c.url === '/api/chat' ? krSession : mnSession)));

    // A reload must not retain a server session while losing the visible history.
    window.fetch = originalFetch;
    sessionStorage.setItem(reportKey, JSON.stringify({results, krSession, mnSession}));
    location.replace('?probe=chatrestore&phase=reloaded');
  } catch (error) { check('probe exception', false, error.stack || String(error)); finish(); }
})();
