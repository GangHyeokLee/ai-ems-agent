/* PH4-02C-2b: the pure run validators of ui/console/api-analysis.js against real B3-2 envelopes and explicit
 * corruptions of them. Nothing is rendered or applied; the page only calls KemsApiAnalysis. */
(async () => {
  'use strict';
  const results = [];
  const check = (name, pass, detail = null) => results.push({name, pass: !!pass, detail});
  const realFetch = window.fetch.bind(window);
  // A top-level const of a classic script is a global lexical name, not a window property.
  const A = typeof KemsApiAnalysis === 'undefined' ? undefined : KemsApiAnalysis;
  const clone = (value) => structuredClone(value);
  const attempt = (fn) => { try { return {value: fn()}; } catch (error) { return {error}; } };
  const isContract = (outcome) => outcome.error instanceof A.Invalid && outcome.error.code === undefined;
  const isExpired = (outcome) => outcome.error instanceof A.Invalid && outcome.error.code === 'RECEIPT_EXPIRED';
  const describe = (outcome) => (outcome.error ? `${outcome.error.constructor.name}${outcome.error.code ? `/${outcome.error.code}` : ''}: ${outcome.error.message}` : 'accepted');
  const validate = (response, request) => attempt(() => A.runResponse(response,
    {gridId: request.gridId, context: request.context}, request.requestId, request.sessionId));
  const deepFrozen = (value) => !value || typeof value !== 'object' || (Object.isFrozen(value) && Object.values(value).every(deepFrozen));
  const OTHER_UUID = '20000000-0000-4000-8000-0000000000c2';

  try {
    const data = await (await realFetch('/__run_validation__/cases', {cache: 'no-store'})).json();
    const {cases, manual} = data;
    check('real B3-2 responses were generated', cases.length >= 19 && manual.length === 5, {cases: cases.length, manual: manual.length});
    let fetches = 0;
    window.fetch = (...args) => { fetches += 1; return realFetch(...args); };
    const dom = document.documentElement.outerHTML;
    const storage = JSON.stringify({...localStorage}) + JSON.stringify({...sessionStorage});
    const originals = JSON.stringify(data);

    const success = (c) => c.expect.status === 'completed' || c.expect.status === 'not_evaluated';
    const withKind = (kind) => (c) => success(c) && c.expect.kinds.includes(kind);
    const chain2 = (c) => c.expect.kinds.length === 2;
    const failure = (c) => !success(c);
    const sens = (r) => r.analysis.results.findIndex((x) => x.kind === 'sensitivity');

    // 1-5: every real response is accepted with exactly its server status, reason, kinds and payloads.
    for (const c of cases) {
      const outcome = validate(c.response, c.request);
      const v = outcome.value;
      const updates = c.response.ui_updates;
      check(`accept ${c.name}`, v && v.status === c.expect.status && v.reasonCode === c.expect.reasonCode
        && JSON.stringify(v.items.map((item) => item.kind)) === JSON.stringify(c.expect.kinds)
        && v.receipts.length === c.expect.kinds.length && v.answer === c.response.answer
        && v.items.every((item, i) => item.receipt === v.receipts[i] && item.line === c.request.context.outage_line_id
          && JSON.stringify(item.payload) === JSON.stringify(updates[i].result) && item.payload !== updates[i].result
          && item.receipt.result_id === updates[i].result_id && item.receipt.producer === 'agent_tool'
          && item.monitored === (item.kind === 'sensitivity' ? c.request.context.monitored_line_id : null))
        && deepFrozen(v) && (v.receipts.length ? v.modelRevision === v.receipts[0].model_revision && Number.isFinite(v.expiresAt)
          : v.modelRevision === null && v.expiresAt === null),
        outcome.error ? describe(outcome) : {status: v.status, reason: v.reasonCode, kinds: v.items.map((item) => item.kind)});
    }

    const mutations = [
      // 6-8: identity and request context
      ['session_id', () => true, (r) => { r.session_id = 'console-tampered'; }],
      ['request_id', () => true, (r) => { r.request_id = OTHER_UUID; }],
      ['grid_id', () => true, (r) => { r.grid_id = 'mn-osm-35k'; }],
      ['context revision', () => true, (r) => { r.analysis_context.revision += 1; }],
      ['context view_id', () => true, (r) => { r.analysis_context.view_id = OTHER_UUID; }],
      ['context outage', () => true, (r) => { r.analysis_context.outage_line_id = r.analysis_context.outage_line_id === 'LINE-1-2' ? 'LINE-16-28' : 'LINE-1-2'; }],
      ['context monitored', () => true, (r) => { r.analysis_context.monitored_line_id = r.analysis_context.monitored_line_id ? null : 'LINE-16-22'; }],
      ['context mode', () => true, (r) => { r.analysis_context.mode = 'reference'; }],
      ['context operation', () => true, (r) => { r.analysis_context.operation = r.analysis_context.operation === 'security' ? 'sensitivity' : 'security'; }],
      ['context references', () => true, (r) => { r.analysis_context.references.security = OTHER_UUID; }],
      ['request snapshot is not a run', () => true, (r, q) => { q.context.mode = 'reference'; r.analysis_context.mode = 'reference'; }],
      // envelope shape
      ['extra envelope key', () => true, (r) => { r.extra = 1; }],
      ['blank answer', () => true, (r) => { r.answer = ' '; }],
      ['extra analysis key', () => true, (r) => { r.analysis.extra = 1; }],
      ['message not text', () => true, (r) => { r.analysis.message = null; }],
      ['ui_updates not a list', () => true, (r) => { r.ui_updates = {}; }],
      ['results not a list', () => true, (r) => { r.analysis.results = {}; }],
      // status rules (verify_evidence / _publish_run)
      ['unknown status', () => true, (r) => { r.analysis.status = 'done'; }],
      ['status flipped', success, (r) => { r.analysis.status = r.analysis.status === 'completed' ? 'not_evaluated' : 'completed'; }],
      ['completed with a reason', (c) => c.expect.status === 'completed', (r) => { r.analysis.reason_code = 'NO_OVERLOAD'; }],
      ['not_evaluated with another reason', (c) => c.expect.status === 'not_evaluated', (r) => { r.analysis.reason_code = 'OTHER_REASON'; }],
      ['source flipped', () => true, (r) => { r.analysis.source = r.analysis.source === 'none' ? 'tool_execution' : 'none'; }],
      ['failure without a reason', failure, (r) => { r.analysis.reason_code = null; }],
      ['failure reason malformed', failure, (r) => { r.analysis.reason_code = 'tool failed'; }],
      ['not_run with another reason', (c) => c.expect.status === 'not_run', (r) => { r.analysis.reason_code = 'TOOL_FAILED'; }],
      ['failure carrying a result', failure, (r) => {
        const donor = cases.find((x) => x.expect.kinds.join() === 'security');
        r.analysis.results = clone(donor.response.analysis.results); r.ui_updates = clone(donor.response.ui_updates);
      }],
      // 9-11: agent receipts
      ['receipt producer', success, (r) => { r.analysis.results[0].producer = 'manual'; }],
      ['receipt producer_request_id', success, (r) => { r.analysis.results[0].producer_request_id = OTHER_UUID; }],
      ['tool_call_id empty', success, (r) => { r.analysis.results[0].tool_call_id = ''; }],
      ['tool_call_id not text', success, (r) => { r.analysis.results[0].tool_call_id = 7; }],
      ['tool_call_id null', success, (r) => { r.analysis.results[0].tool_call_id = null; }],
      ['tool_call_id repeated in a chain', chain2, (r) => { r.analysis.results[1].tool_call_id = r.analysis.results[0].tool_call_id; }],
      ['model_revision malformed', success, (r) => { r.analysis.results[0].model_revision = 'abc'; }],
      ['model_revision differs within a chain', chain2, (r) => {
        const second = r.analysis.results[1]; second.model_revision = second.model_revision === 'f'.repeat(64) ? 'e'.repeat(64) : 'f'.repeat(64);
      }],
      ['receipt kind', success, (r) => { const x = r.analysis.results[0]; x.kind = x.kind === 'security' ? 'sensitivity' : 'security'; }],
      ['receipt outage', success, (r) => { r.analysis.results[0].outage_line_id = 'LINE-99-99'; }],
      ['receipt grid', success, (r) => { r.analysis.results[0].grid_id = 'mn-osm-35k'; }],
      ['receipt assessment flipped', success, (r) => { const x = r.analysis.results[0]; x.assessment = x.assessment === 'completed' ? 'not_evaluated' : 'completed'; }],
      ['receipt reason changed', success, (r) => { const x = r.analysis.results.at(-1); x.reason_code = x.reason_code ? 'OTHER_REASON' : 'NO_OVERLOAD'; }],
      ['receipt extra key', success, (r) => { r.analysis.results[0].session_id = 'console-x'; }],
      ['receipt result_id malformed', success, (r) => { r.analysis.results[0].result_id = 'not-a-uuid'; }],
      ['receipt time malformed', success, (r) => { r.analysis.results[0].created_at = '2026-11-05 07:00:00'; }],
      ['receipt validity reversed', success, (r) => { const x = r.analysis.results[0]; [x.created_at, x.expires_at] = [x.expires_at, x.created_at]; }],
      ['sensitivity top_n', withKind('sensitivity'), (r) => { r.analysis.results[sens(r)].top_n = 3; }],
      ['sensitivity selection flipped', withKind('sensitivity'), (r) => {
        const x = r.analysis.results[sens(r)]; x.monitored_selection = x.monitored_selection === 'auto' ? 'explicit' : 'auto';
      }],
      ['sensitivity target replaced', withKind('sensitivity'), (r) => {
        const x = r.analysis.results[sens(r)]; x.monitored_line_id = x.monitored_line_id === 'LINE-58-59' ? 'LINE-1-2' : 'LINE-58-59';
      }],
      ['security receipt names a target', withKind('security'), (r) => { r.analysis.results[0].monitored_line_id = 'LINE-16-22'; }],
      // 12-13: ui_updates against the receipts
      ['ui_update missing', success, (r) => { r.ui_updates.pop(); }],
      ['ui_update duplicated', success, (r) => { r.ui_updates.push(clone(r.ui_updates[0])); }],
      ['ui_updates reordered', chain2, (r) => { r.ui_updates.reverse(); }],
      ['results and ui_updates reordered together', chain2, (r) => { r.analysis.results.reverse(); r.ui_updates.reverse(); }],
      ['chain cut after its N-1', chain2, (r) => { r.analysis.results.pop(); r.ui_updates.pop(); }],
      ['ui_update type', success, (r) => { const u = r.ui_updates[0]; u.type = u.type === 'security' ? 'sensitivity' : 'security'; }],
      ['ui_update grid', success, (r) => { r.ui_updates[0].grid_id = 'mn-osm-35k'; }],
      ['ui_update extra key', success, (r) => { r.ui_updates[0].receipt = 'x'; }],
      ['ui_update result_id', success, (r) => { r.ui_updates[0].result_id = OTHER_UUID; }],
      ['result_ids swapped within a chain', chain2, (r) => { const [a, b] = r.ui_updates; [a.result_id, b.result_id] = [b.result_id, a.result_id]; }],
      // 14: the canonical result data
      ['result not an object', success, (r) => { r.ui_updates[0].result = []; }],
      ['result outage', success, (r) => { r.ui_updates[0].result.outage_line_id = 'LINE-99-99'; }],
      ['result status', success, (r) => {
        const p = r.ui_updates.at(-1).result; p.analysis_status = p.analysis_status === 'COMPLETED' ? 'NO_OVERLOAD' : 'COMPLETED';
      }],
      ['result monitored', withKind('sensitivity'), (r) => {
        const p = r.ui_updates[sens(r)].result; p.monitored_line_id = p.monitored_line_id ? 'LINE-58-59' : 'LINE-16-22';
      }],
      ['result candidate count', withKind('sensitivity'), (r) => { r.ui_updates[sens(r)].result.candidate_count += 1; }],
      ['result candidates invented', (c) => success(c) && c.response.ui_updates.some((u) => u.type === 'sensitivity' && u.result.analysis_status !== 'COMPLETED'),
        (r) => { const p = r.ui_updates[sens(r)].result; p.candidates = [{generator_id: 'GEN-X', rank: 1}]; p.candidate_count = 1; }],
      ['result violations not a list', withKind('security'), (r) => { r.ui_updates[0].result.violations = 'none'; }],
      ['result assessment flag flipped', withKind('security'), (r) => {
        const p = r.ui_updates[0].result; p.violation_assessment_available = !p.violation_assessment_available;
      }],
      ['result convergence not boolean', withKind('security'), (r) => { r.ui_updates[0].result.base_converged = 'yes'; }],
    ];
    for (const [name, applies, mutate] of mutations) {
      const failures = [];
      let applied = 0;
      for (const c of cases.filter(applies)) {
        const response = clone(c.response);
        const request = clone(c.request);
        mutate(response, request);
        applied += 1;
        const outcome = validate(response, request);
        if (!isContract(outcome)) failures.push({case: c.name, got: describe(outcome)});
      }
      check(`reject ${name}`, applied > 0 && failures.length === 0, {applied, failures});
    }

    // 15: expiry is named only after the whole contract matched; it never hides a violation.
    const age = (r, indexes = r.analysis.results.map((_, i) => i)) => {
      for (const i of indexes) {
        const x = r.analysis.results[i];
        x.created_at = new Date(Date.parse(x.created_at) - 7200000).toISOString();
        x.expires_at = new Date(Date.parse(x.expires_at) - 7200000).toISOString();
      }
    };
    const expiry = [
      ['expired receipts are named as expiry', success, (r) => age(r), isExpired],
      ['one expired receipt of a chain is enough', chain2, (r) => age(r, [1]), isExpired],
      ['expiry does not hide a session mismatch', success, (r) => { age(r); r.session_id = 'console-tampered'; }, isContract],
      ['expiry does not hide a result change', success, (r) => { age(r); r.ui_updates[0].result.outage_line_id = 'LINE-99-99'; }, isContract],
      ['expiry does not hide a result_id change', success, (r) => { age(r); r.ui_updates[0].result_id = OTHER_UUID; }, isContract],
      ['expiry does not hide a receipt change', success, (r) => { age(r); r.analysis.results[0].producer = 'manual'; }, isContract],
    ];
    for (const [name, applies, mutate, expected] of expiry) {
      const failures = [];
      let applied = 0;
      for (const c of cases.filter(applies)) {
        const response = clone(c.response);
        mutate(response);
        applied += 1;
        const outcome = validate(response, c.request);
        if (!expected(outcome)) failures.push({case: c.name, got: describe(outcome)});
      }
      check(name, applied > 0 && failures.length === 0, {applied, failures});
    }

    // The shared receipt checks keep the C-1 manual decisions; producers are not interchangeable.
    for (const m of manual) {
      const outcome = attempt(() => A.manualReceipt(m.payload, m.kind, m.request));
      check(`manual receipt still accepted ${m.name}`, outcome.value && outcome.value.result_id === m.payload.analysis_receipt.result_id
        && Object.isFrozen(outcome.value), describe(outcome));
    }
    const manualMutations = [
      ['producer', (p) => { p.analysis_receipt.producer = 'agent_tool'; }],
      ['tool_call_id', (p) => { p.analysis_receipt.tool_call_id = 'call-1'; }],
      ['assessment', (p) => { const x = p.analysis_receipt; x.assessment = x.assessment === 'completed' ? 'not_evaluated' : 'completed'; }],
      ['extra key', (p) => { p.analysis_receipt.session_id = 'console-x'; }],
      ['revision', (p) => { p.analysis_receipt.model_revision = 'abc'; }],
      ['time', (p) => { p.analysis_receipt.expires_at = p.analysis_receipt.created_at; }],
      ['outage', (p) => { p.analysis_receipt.outage_line_id = 'LINE-99-99'; }],
    ];
    for (const [name, mutate] of manualMutations) {
      const failures = manual.filter((m) => {
        const payload = clone(m.payload);
        mutate(payload);
        return !isContract(attempt(() => A.manualReceipt(payload, m.kind, m.request)));
      }).map((m) => m.name);
      check(`manual receipt still rejects ${name}`, failures.length === 0, failures);
    }
    const agent = cases.find((c) => c.expect.kinds.join() === 'security' && c.expect.status === 'completed');
    const manualSecurity = manual.find((m) => m.kind === 'security' && m.payload.analysis_status === 'COMPLETED');
    check('an agent receipt is not a manual receipt', isContract(attempt(() => A.manualReceipt(
      {...agent.response.ui_updates[0].result, analysis_receipt: agent.response.analysis.results[0]}, 'security',
      {gridId: 'kr-kpg193', line: agent.request.context.outage_line_id, monitored: null, requestId: agent.request.requestId}))));
    check('a manual receipt is not an agent receipt', isContract(attempt(() => A.agentReceipt(manualSecurity.payload.analysis_receipt, {
      kind: 'security', gridId: 'kr-kpg193', line: manualSecurity.request.line, monitored: null,
      requestId: manualSecurity.request.requestId, payload: manualSecurity.payload}))));

    // Purity: the validators made no request, wrote no DOM or storage and never changed or froze their inputs.
    check('validators made no request and changed no DOM or storage', fetches === 0 && document.documentElement.outerHTML === dom
      && JSON.stringify({...localStorage}) + JSON.stringify({...sessionStorage}) === storage, {fetches});
    check('inputs were neither modified nor frozen', JSON.stringify(data) === originals
      && !Object.isFrozen(agent.response) && !Object.isFrozen(agent.response.ui_updates[0].result));
  } catch (error) {
    check('probe exception', false, String(error.stack || error));
  } finally {
    document.getElementById('status').textContent = 'done';
    await realFetch('/__run_validation__/report', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({results, errors: window.__runValidationErrors})});
  }
})();
