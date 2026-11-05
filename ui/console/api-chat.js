/* Grid-scoped basic chat, opt-in C-1 reference and C-2c AI run transport.
 * The bridge owns analysis context and validates reference envelopes; this module never applies map updates.
 * C-2a: a v1 request keeps its frozen bridge snapshot in request.analysis (context.mode says which kind;
 * 'reference' or 'run'). Pending-request locks and cancellation use that one field.
 * C-2c: a run response is validated and applied by the bridge (applyRun), all or nothing; a rejected or
 * expired run response changes nothing and keeps the session, since the server already finished it.
 * Sessions live in this page only. Aborting HTTP does not cancel the server Agent:
 * every abandoned/failed request retires its session before another turn is sent.
 */
const KemsApiChat = (() => {
  'use strict';

  const ENDPOINTS = Object.freeze({
    'kr-kpg193': '/api/grids/kr-kpg193/chat',
    'mn-osm-35k': '/api/grids/mn-osm-35k/chat',
  });
  const TIMEOUT_MS = 60000;
  const supported = (gridId, entry) => Object.hasOwn(ENDPOINTS, gridId) && entry?.chat_available === true;
  const sessionId = () => `console-${Array.from(crypto.getRandomValues(new Uint32Array(4)), (n) => n.toString(16).padStart(8, '0')).join('')}`;
  const uuid = () => {
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 15) | 64;
    bytes[8] = (bytes[8] & 63) | 128;
    const hex = Array.from(bytes, (n) => n.toString(16).padStart(2, '0')).join('');
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  };
  const ERROR_TEXT = Object.freeze({
    ANALYSIS_REFERENCE_UNAVAILABLE: '참조할 분석 결과가 없거나 만료되었습니다. 다시 분석해야 참조 가능합니다.',
    ANALYSIS_REFERENCE_STALE: '모델 상태가 변경되었습니다. 다시 분석해야 참조 가능합니다.',
    REQUEST_REPLAY_STALE: '이전 분석 결과가 만료되었거나 모델 상태가 변경되었습니다.',
    REQUEST_IN_PROGRESS: '같은 요청을 처리 중입니다. 자동 재전송하지 않았습니다.',
    REQUEST_ID_REUSE: '요청 식별자가 일치하지 않습니다. 새 요청이 필요합니다.',
    SESSION_BUSY: '현재 대화의 다른 요청을 처리 중입니다. 잠시 후 다시 시도하세요.',
    ANALYSIS_BUSY: '모델에서 다른 계산이 진행 중입니다. 잠시 후 다시 시도하세요.',
    MODEL_IDENTITY_UNAVAILABLE: '모델 상태를 확인할 수 없어 결과를 참조할 수 없습니다.',
    ANALYSIS_CONTEXT_UNSUPPORTED: '이 계통은 기존 결과 설명을 지원하지 않습니다.',
    MODEL_REVISION_CHANGED: '계산 중 모델 상태가 변경되어 AI 참조용 결과로 등록하지 못했습니다. 수동 분석 결과는 표시하지만 기존 결과 설명은 사용할 수 없습니다.',
    RESULT_NOT_REGISTERED: '분석 결과를 AI 참조용으로 등록하지 못했습니다. 다시 분석해야 참조 가능합니다.',
    ANALYSIS_RUN_NOT_READY: '서버가 AI 분석 실행을 허용하지 않는 상태입니다. 운영 준비(단일 worker·실행 예산)를 확인하세요.',
    ANALYSIS_CONTEXT_INVALID: '분석 문맥 요청 형식이 올바르지 않아 실행하지 않았습니다.',
    AGENT_UNAVAILABLE: 'AI 모델 연결 또는 응답 오류로 분석을 실행하지 못했습니다. 기존 분석 결과는 바뀌지 않았습니다.',
    AGENT_TIMEOUT: 'AI 모델 응답 시간이 초과되어 분석을 실행하지 못했습니다. 기존 분석 결과는 바뀌지 않았습니다.',
    AGENT_BUDGET_EXCEEDED: '분석 실행 예산(시간·단계)을 넘어 실행을 중단했습니다. 기존 분석 결과는 바뀌지 않았습니다.',
    AGENT_FAILED: 'AI 분석 실행 중 오류가 발생했습니다. 기존 분석 결과는 바뀌지 않았습니다.',
  });
  const errorText = (detail) => {
    const code = typeof detail === 'string' ? detail : detail?.code;
    return Object.hasOwn(ERROR_TEXT, code) ? ERROR_TEXT[code] : null;
  };
  const node = (tag, attrs, text) => {
    const element = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) element.setAttribute(key, value);
    if (text !== undefined) element.textContent = text;
    return element;
  };

  class ChatError extends Error {}
  async function post(gridId, message, request) {
    const body = {session_id: request.sessionId, message, grid_id: gridId};
    if (request.analysis) Object.assign(body, {request_id: request.id, analysis_context: request.analysis.context});
    const response = await fetch(ENDPOINTS[gridId], {
      method: 'POST', signal: request.controller.signal, cache: 'no-store',
      headers: {'Content-Type': 'application/json', Accept: 'application/json'},
      body: JSON.stringify(body),
    });
    // Server exception details can contain deployment information; display fixed guidance only.
    if (!response.ok) {
      let detail;
      try { detail = (await response.json())?.detail; } catch (_) { /* fixed fallback below */ }
      const reason = [502, 503, 504].includes(response.status)
        ? 'AI 서비스 연결 또는 응답에 실패했습니다. LLM과 계통 데이터 상태를 확인하세요.'
        : response.status >= 500 ? '서버에서 AI 요청을 처리하지 못했습니다.'
          : 'AI 요청이 거부되었습니다. 계통과 입력 내용을 확인하세요.';
      throw new ChatError(`${errorText(detail) || reason} (HTTP ${response.status})`);
    }
    let payload;
    try { payload = await response.json(); } catch (error) { throw new ChatError('AI 응답 형식이 올바르지 않습니다.'); }
    if (!payload || payload.grid_id !== gridId || payload.session_id !== request.sessionId
        || typeof payload.answer !== 'string' || !payload.answer.trim()) {
      throw new ChatError('AI 응답의 계통·세션 또는 답변 형식이 일치하지 않습니다.');
    }
    return payload;
  }

  function create(host, hooks = {}) {
    const sessions = new Map();
    let context = null;
    const title = node('h3', {id: 'kemsChatTitle'}, 'AI 채팅');
    const reset = node('button', {id: 'kemsChatReset', type: 'button', class: 'btn'}, '대화 초기화');
    reset.title = '현재 계통의 대화만 초기화합니다. 지도와 분석 결과는 유지됩니다.';
    const heading = node('div', {class: 'kems-chathead'});
    heading.append(title, reset);
    const messages = node('div', {id: 'kemsChatMessages', class: 'kems-chatmessages', role: 'log', 'aria-live': 'polite', 'aria-label': '현재 계통 대화'});
    const status = node('p', {id: 'kemsChatStatus', class: 'kems-chatstatus', role: 'status'});
    const input = node('textarea', {id: 'kemsChatInput', rows: '2', autocomplete: 'off', 'aria-label': 'EMS AI 질의 입력', 'aria-describedby': 'kemsChatStatus', placeholder: '선택한 계통에 대해 질문하세요'});
    const send = node('button', {id: 'kemsChatSend', type: 'submit', class: 'btn pri'}, '보내기');
    const form = node('form', {id: 'kemsChatForm', class: 'kems-chatinput'});
    const modes = node('select', {id: 'kemsChatMode', 'aria-label': '채팅 모드'});
    const runMode = node('option', {value: 'run', hidden: true, disabled: true}, 'AI 분석 실행');
    modes.append(node('option', {value: 'basic'}, '기본 채팅'), node('option', {value: 'reference'}, '기존 결과 설명'), runMode);
    const operation = node('select', {id: 'kemsChatOperation', 'aria-label': '분석 종류'});
    const OPERATION_LABELS = {security: ['N-1 결과', 'N-1 실행'], sensitivity: ['Sensitivity 결과', 'Sensitivity 실행'],
      security_then_sensitivity: ['N-1 + Sensitivity 결과', 'N-1 → Sensitivity 실행']};
    for (const [value, [label]] of Object.entries(OPERATION_LABELS)) operation.append(node('option', {value}, label));
    const referenceStatus = node('p', {id: 'kemsReferenceStatus', role: 'status'});
    const referenceControls = node('div', {class: 'kems-reference', hidden: true});
    referenceControls.append(modes, operation, referenceStatus);
    form.append(input, send);
    host.setAttribute('aria-labelledby', 'kemsChatTitle');
    host.append(heading, referenceControls, messages, status, form);
    let expiryTimer = null;

    function currentSession() {
      if (!context) return null;
      if (!sessions.has(context.gridId)) sessions.set(context.gridId, {id: sessionId(), messages: [], draft: '', pending: null, error: ''});
      return sessions.get(context.gridId);
    }

    function render() {
      const state = currentSession();
      const enabled = Boolean(context?.enabled && Object.hasOwn(ENDPOINTS, context.gridId));
      const pending = Boolean(state?.pending);
      const reference = hooks.referenceState?.();
      clearTimeout(expiryTimer);
      referenceControls.hidden = !reference?.supported;
      // The run mode is offered only with its own advertisement (never on reference support alone).
      runMode.hidden = runMode.disabled = !reference?.modes?.run;
      modes.value = reference?.mode || 'basic';
      operation.value = reference?.operation || 'security';
      operation.hidden = modes.value === 'basic';
      for (const option of operation.options) {
        option.disabled = !reference?.options?.[option.value]?.ok;
        option.textContent = OPERATION_LABELS[option.value][modes.value === 'run' ? 1 : 0];
      }
      // A choice moves the analysis context and would cancel a pending v1 request (retiring the
      // session and its receipts), so both selectors wait for its success, failure or timeout.
      modes.disabled = operation.disabled = Boolean(state?.pending?.analysis);
      referenceStatus.textContent = reference?.reason || '';
      if (Number.isFinite(reference?.expiresAt) && reference.expiresAt > Date.now()) {
        expiryTimer = setTimeout(render, Math.min(reference.expiresAt - Date.now() + 1, 600001));
      }
      host.dataset.gridId = context?.gridId || '';
      host.dataset.busy = String(pending);
      title.textContent = context ? `${context.name} · AI 채팅` : 'AI 채팅';
      input.disabled = send.disabled = !enabled || pending;
      if ((reference?.mode === 'reference' || reference?.mode === 'run') && !reference.ready) send.disabled = true;
      reset.disabled = !enabled;
      if (input.value !== (state?.draft || '')) input.value = state?.draft || '';
      send.textContent = pending ? '응답 대기…' : '보내기';
      const nearEnd = messages.scrollHeight - messages.scrollTop - messages.clientHeight < 60;
      messages.replaceChildren(...(state?.messages || []).map((message) => {
        const item = node('div', {class: `kems-chatmessage kems-chat-${message.role}`, 'data-role': message.role});
        item.append(node('b', {}, {user: '사용자', assistant: 'AI', system: '안내'}[message.role]), node('div', {}, message.text));
        return item;
      }));
      if (nearEnd || pending) messages.scrollTop = messages.scrollHeight;
      status.classList.toggle('kems-err', Boolean(enabled && state?.error));
      status.textContent = !context ? '' : !enabled ? context.reason || '이 계통은 AI 채팅을 지원하지 않습니다.'
        : pending ? 'AI 응답을 기다리고 있습니다. 최대 60초가 걸릴 수 있습니다.'
          : state.error || '대화는 계통별로 유지되며 페이지를 새로 열면 초기화됩니다. Shift+Enter로 줄바꿈합니다.';
    }

    // The bridge re-renders its manual controls when a run starts or ends (they are locked meanwhile).
    const runChanged = (request) => { if (request?.analysis?.context.mode === 'run') hooks.runPendingChanged?.(); };

    function retire(state, notice) {
      const request = state.pending;
      state.pending = null;  // invalidate before abort/cancel callbacks run
      state.id = sessionId();
      hooks.sessionRetired?.(context?.gridId);
      if (request) {
        clearTimeout(request.timer);
        request.controller.abort();
        request.cancel();
      }
      if (notice) state.messages.push({role: 'system', text: notice});
      runChanged(request);
    }

    async function submit(event) {
      event.preventDefault();
      const state = currentSession();
      if (!context?.enabled || !Object.hasOwn(ENDPOINTS, context.gridId) || !state || state.pending) return;
      const message = input.value.trim();
      if (!message) return;
      const gridId = context.gridId;
      const request = {sessionId: state.id, controller: new AbortController(), cancel: null, timer: null};
      const mode = hooks.referenceState?.().mode;
      if (mode === 'reference' || mode === 'run') {
        try {
          if (message.length > 8000) throw new Error(`${mode === 'run' ? 'AI 분석 실행 요청' : '기존 결과 설명'}은 8000자 이내로 입력하세요.`);
          request.analysis = mode === 'run' ? hooks.captureRun() : hooks.captureReference();
          request.id = uuid();
        } catch (error) {
          state.error = error.message;
          render();
          return;
        }
      }
      const deadline = new Promise((resolve, reject) => {
        request.cancel = () => reject(new ChatError('취소된 요청'));
        request.timer = setTimeout(() => {
          // Reject independently of fetch: stale transports cannot leave the UI waiting forever.
          reject(new ChatError(request.analysis?.context.mode === 'run'
            ? 'AI 분석 실행 응답 시간이 초과되었습니다 (60초). 서버 계산은 계속될 수 있으며 결과는 반영하지 않았습니다.'
            : 'AI 응답 시간이 초과되었습니다 (60초).'));
          request.controller.abort();
        }, TIMEOUT_MS);
      });
      const current = () => context?.gridId === gridId && context.enabled && state.pending === request && state.id === request.sessionId
        && (!request.analysis || (request.analysis.context.mode === 'run' ? hooks.runCurrent : hooks.referenceCurrent)(request.analysis));
      state.pending = request;
      state.error = '';
      state.draft = '';
      state.messages.push({role: 'user', text: message});
      render();
      runChanged(request);
      try {
        const payload = await Promise.race([post(gridId, message, request), deadline]);
        if (!current()) return;
        if (request.analysis?.context.mode === 'run') {
          let outcome;
          try {
            outcome = hooks.applyRun(payload, request);
          } catch (error) {
            // Nothing was applied and the server already answered: report it and keep the session (and receipts).
            state.error = error?.code === 'RECEIPT_EXPIRED' ? '분석 결과가 만료되었습니다. 기존 분석 결과를 유지했습니다.'
              : 'AI 분석 실행 응답이 요청 문맥·Receipt 계약과 일치하지 않아 전체를 거부했습니다. 기존 분석 결과를 유지했습니다.';
            state.messages.push({role: 'system', text: state.error});
            return;
          }
          if (!outcome.stale) state.messages.push({role: 'assistant', text: `${outcome.label}\n${payload.answer}`});
          return;
        }
        if (request.analysis) {  // a reference
          try { hooks.validateReference(payload, request); }
          catch (error) {
            // Both are rejections; only a receipt that expired in flight (checked after the full contract) is named as such.
            throw new ChatError(error?.code === 'RECEIPT_EXPIRED' ? '분석 결과가 만료되었습니다. 다시 분석해야 참조 가능합니다.'
              : '기존 결과 설명 응답이 요청 문맥·Receipt 계약과 일치하지 않습니다.');
          }
        }
        const answer = request.analysis ? `기존 결과 설명 · 새 계산 없음\n${payload.answer}` : payload.answer;
        state.messages.push({role: 'assistant', text: answer});
      } catch (error) {
        if (!current()) return;
        const description = error instanceof ChatError ? error.message : 'EMS AI 서버에 연결할 수 없습니다. 연결 상태를 확인하세요.';
        state.error = description;
        retire(state, `${description} 다음 질문은 새 서버 대화로 시작합니다.`);
      } finally {
        clearTimeout(request.timer);
        if (state.pending === request) {
          state.pending = null;
          runChanged(request);
        }
        if (context?.gridId === gridId) render();
      }
    }

    input.addEventListener('input', () => { const state = currentSession(); if (state) state.draft = input.value; });
    input.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && event.keyCode !== 229) {
        event.preventDefault();
        form.requestSubmit();
      }
    });
    form.addEventListener('submit', submit);
    for (const control of [modes, operation]) control.addEventListener('change', () => {
      hooks.referenceChoice?.(modes.value, operation.value);
      render();
    });
    reset.addEventListener('click', () => {
      if (!context?.enabled || !Object.hasOwn(ENDPOINTS, context.gridId)) return;
      const state = currentSession();
      retire(state);
      state.messages = [];
      state.draft = '';
      state.error = '';
      render();
      input.focus();
    });

    function setContext(next) {
      const changed = context?.gridId !== next?.gridId || context?.enabled !== next?.enabled
        || context?.name !== next?.name || context?.reason !== next?.reason || context?.viewId !== next?.viewId
        || context?.analysisRevision !== next?.analysisRevision || context?.v1 !== next?.v1;
      if (!changed) return;  // analysis renders must not rebuild the composer or disturb its focus
      if (context && (context.gridId !== next?.gridId || !next?.enabled || context.viewId !== next?.viewId)) {
        const previous = currentSession();
        if (previous.pending) retire(previous, '계통 전환 또는 기능 상태 변경으로 응답 대기를 취소했습니다. 다음 질문은 새 서버 대화로 시작합니다.');
      }
      context = next;
      render();
    }

    render();
    return Object.freeze({setContext, refresh: () => render(),
      getSessionId: (gridId) => gridId === context?.gridId ? currentSession()?.id : null,
      // The frozen v1 snapshot of the request pending on this grid, if any (read-only for the bridge).
      pendingAnalysis: (gridId) => (gridId === context?.gridId ? currentSession()?.pending?.analysis || null : null),
      invalidateAnalysisContext: () => {
        const state = currentSession();
        if (state?.pending?.analysis) {
          retire(state, state.pending.analysis.context.mode === 'run'
            ? '분석 문맥 변경으로 AI 분석 실행 대기를 취소했습니다. 서버 계산은 계속될 수 있으며 결과는 반영하지 않습니다.'
            : '분석 문맥 변경으로 기존 결과 설명 대기를 취소했습니다.');
        }
      },
    });
  }
  return Object.freeze({create, supported, uuid, errorText});
})();
