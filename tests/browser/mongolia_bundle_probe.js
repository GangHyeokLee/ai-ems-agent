// Actual standalone app and HTTP chat; only the local Ollama provider is scripted.
(async () => {
  const results = [];
  const check = (name, value, detail) => results.push({name, pass: !!value, detail: detail ?? null});
  const $ = id => document.getElementById(id);
  const wait = async predicate => {
    const started = performance.now();
    while (!predicate()) {
      if (performance.now() - started > 90000) throw new Error('UI state timeout');
      await new Promise(resolve => setTimeout(resolve, 40));
    }
  };
  try {
    await wait(() => currentMapData && pfPreview && gridEntries.length === 3);
    check('KPG and KR and MN selectable', ['kr-kpg193', 'kr-osm-transmission', 'mn-osm-35k'].every(id => gridEntries.some(g => g.id === id && g.map_available)));
    $('grid-select').value = 'mn-osm-35k';
    $('grid-select').dispatchEvent(new Event('change'));
    await wait(() => selectedGridId === 'mn-osm-35k' && currentMapData?.buses.length === 173 && pfPreview?.metadata.grid_id === 'mn-osm-35k');
    check('MN map 173/159/27', currentMapData.buses.length === 173 && currentMapData.lines.length === 159 && currentMapData.transformers.length === 27);
    check('Map painted', document.querySelectorAll('.network-line').length > 0);
    check('PF scenario and analysis 166/156/27', pfPreview.metadata.scenario_id === 'reduced_27_q_limited_with_reactor' && pfPreview.buses.length === 166 && pfPreview.lines.length === 156 && pfPreview.transformers.length === 27);
    check('Public records retained', currentMapData.powerplants.length === 27);
    check('Synthetic disclaimer displayed', $('pf-preview-disclaimer').textContent.includes('실제 몽골 운영 데이터가 아닙니다.'));
    check('Unverified plant connections displayed', $('pf-preview-limit-note').textContent.includes('실제 발전소 접속 미확인'));
    const diagnostic = await fetch('/api/grids/mn-osm-35k/runtime-status').then(r => r.json());
    check('Bundle ready', diagnostic.validation_status === 'VERIFIED' && diagnostic.numerical_checks === 186, diagnostic.bundle_id);
    $('chat-message').value = '751261406-1_0 N-1 후 민감도 Top 5';
    await sendChatMessage();
    check('Actual chat updates Security and Sensitivity', !!$('security-result').querySelector('.result-card') && !!$('sensitivity-result').querySelector('.result-card'));
    check('N-1 correct grid and convergence', latestSecurityResult?.grid_id === 'mn-osm-35k' && latestSecurityResult?.post_status === 'CONVERGED');
    check('Sensitivity five candidates rendered', document.querySelectorAll('#sensitivity-result .result-item').length === 5);
    check('Chat synthetic caveat', $('chat-log').textContent.includes('합성'));
  } catch (error) {
    check('probe exception', false, error.stack || String(error));
  }
  window.__krReport = {results};
})();
