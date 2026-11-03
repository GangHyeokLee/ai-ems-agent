/* PH4-01: grid-scoped chat using the existing EMS Agent routes.
 * No analysis context is sent and no ui_updates are applied (PH4-02).
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
  const node = (tag, attrs, text) => {
    const element = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) element.setAttribute(key, value);
    if (text !== undefined) element.textContent = text;
    return element;
  };

  class ChatError extends Error {}
  async function post(gridId, message, request) {
    const response = await fetch(ENDPOINTS[gridId], {
      method: 'POST', signal: request.controller.signal, cache: 'no-store',
      headers: {'Content-Type': 'application/json', Accept: 'application/json'},
      body: JSON.stringify({session_id: request.sessionId, message, grid_id: gridId}),
    });
    // Server exception details can contain deployment information; display fixed guidance only.
    if (!response.ok) {
      const reason = [502, 503, 504].includes(response.status)
        ? 'AI 서비스 연결 또는 응답에 실패했습니다. LLM과 계통 데이터 상태를 확인하세요.'
        : response.status >= 500 ? '서버에서 AI 요청을 처리하지 못했습니다.'
          : 'AI 요청이 거부되었습니다. 계통과 입력 내용을 확인하세요.';
      throw new ChatError(`${reason} (HTTP ${response.status})`);
    }
    let payload;
    try { payload = await response.json(); } catch (error) { throw new ChatError('AI 응답 형식이 올바르지 않습니다.'); }
    if (!payload || payload.grid_id !== gridId || payload.session_id !== request.sessionId
        || typeof payload.answer !== 'string' || !payload.answer.trim()) {
      throw new ChatError('AI 응답의 계통·세션 또는 답변 형식이 일치하지 않습니다.');
    }
    return payload.answer;  // ui_updates intentionally unused
  }

  function create(host) {
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
    form.append(input, send);
    host.setAttribute('aria-labelledby', 'kemsChatTitle');
    host.append(heading, messages, status, form);

    function currentSession() {
      if (!context) return null;
      if (!sessions.has(context.gridId)) sessions.set(context.gridId, {id: sessionId(), messages: [], draft: '', pending: null, error: ''});
      return sessions.get(context.gridId);
    }

    function render() {
      const state = currentSession();
      const enabled = Boolean(context?.enabled && Object.hasOwn(ENDPOINTS, context.gridId));
      const pending = Boolean(state?.pending);
      host.dataset.gridId = context?.gridId || '';
      host.dataset.busy = String(pending);
      title.textContent = context ? `${context.name} · AI 채팅` : 'AI 채팅';
      input.disabled = send.disabled = !enabled || pending;
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

    function retire(state, notice) {
      const request = state.pending;
      state.pending = null;  // invalidate before abort/cancel callbacks run
      state.id = sessionId();
      if (request) {
        clearTimeout(request.timer);
        request.controller.abort();
        request.cancel();
      }
      if (notice) state.messages.push({role: 'system', text: notice});
    }

    async function submit(event) {
      event.preventDefault();
      const state = currentSession();
      if (!context?.enabled || !Object.hasOwn(ENDPOINTS, context.gridId) || !state || state.pending) return;
      const message = input.value.trim();
      if (!message) return;
      const gridId = context.gridId;
      const request = {sessionId: state.id, controller: new AbortController(), cancel: null, timer: null};
      const deadline = new Promise((resolve, reject) => {
        request.cancel = () => reject(new ChatError('취소된 요청'));
        request.timer = setTimeout(() => {
          // Reject independently of fetch: stale transports cannot leave the UI waiting forever.
          reject(new ChatError('AI 응답 시간이 초과되었습니다 (60초).'));
          request.controller.abort();
        }, TIMEOUT_MS);
      });
      const current = () => context?.gridId === gridId && context.enabled && state.pending === request && state.id === request.sessionId;
      state.pending = request;
      state.error = '';
      state.draft = '';
      state.messages.push({role: 'user', text: message});
      render();
      try {
        const answer = await Promise.race([post(gridId, message, request), deadline]);
        if (!current()) return;
        state.messages.push({role: 'assistant', text: answer});
      } catch (error) {
        if (!current()) return;
        const description = error instanceof ChatError ? error.message : 'EMS AI 서버에 연결할 수 없습니다. 연결 상태를 확인하세요.';
        state.error = description;
        retire(state, `${description} 다음 질문은 새 서버 대화로 시작합니다.`);
      } finally {
        clearTimeout(request.timer);
        if (state.pending === request) state.pending = null;
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
        || context?.name !== next?.name || context?.reason !== next?.reason;
      if (!changed) return;  // analysis renders must not rebuild the composer or disturb its focus
      if (context && (context.gridId !== next?.gridId || !next?.enabled)) {
        const previous = currentSession();
        if (previous.pending) retire(previous, '계통 전환 또는 기능 상태 변경으로 응답 대기를 취소했습니다. 다음 질문은 새 서버 대화로 시작합니다.');
      }
      context = next;
      render();
    }

    render();
    return Object.freeze({setContext});
  }
  return Object.freeze({create, supported});
})();
