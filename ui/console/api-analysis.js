/* K-EMS console: N-1 Security Analysis and Sensitivity Analysis of an EMS API grid (Phase 3).
 *
 * api-bridge.js owns grid selection, the requests, their cancellation and the analysis state. This
 * module owns the per-grid API contracts and how their results are shown:
 *   - request URLs, only for the grids whose EMS backend runs the analyses (KPG-193, Mongolia OSM).
 *     A grid without an adapter (the map-only Korean OSM grid) is never sent an analysis request;
 *   - validation of every response against its request and the map on screen, normalized into a
 *     view model that carries only values present in the response. The two backends differ and are
 *     kept apart: KPG returns the violations only; Mongolia also returns islanding, post-contingency
 *     extremes, the sensitivity definition and its unit;
 *   - the analysis block of the line card and the 의사결정지원 sections, written as text nodes only;
 *   - the map overlay: outage line, violated equipment, islands, monitored line and candidate buses.
 *     No API returns the post-contingency state of every line, so nothing else is coloured.
 * Nothing here computes power flow, fills in a value missing from a response or calls fetch().
 */
const KemsApiAnalysis = (() => {
  'use strict';

  const TOP_N = 5;
  const TIMEOUT_MS = Object.freeze({security: 60000, sensitivity: 90000});

  /* ---------- small helpers (text only, never innerHTML) ---------- */
  function h(tag, attrs, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value === null || value === undefined || value === false) continue;
      if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value === true ? '' : String(value));
    }
    node.append(...children.flat(Infinity).filter((child) => child !== null && child !== undefined && child !== false));
    return node;
  }
  const finite = (value) => typeof value === 'number' && Number.isFinite(value);
  const idOf = (value) => (value === null || value === undefined ? '' : String(value));
  const num = (value, digits) => value.toLocaleString('ko-KR', {minimumFractionDigits: digits, maximumFractionDigits: digits});
  const fixed = (value, digits, unit) => (finite(value) ? `${num(value, digits)}${unit ? ` ${unit}` : ''}` : 'N/A');
  const pct = (value) => (finite(value) ? `${num(value, 2)}%` : 'N/A');  // as the bridge writes 98.5%
  const row = (key, value) => (value === null || value === undefined || value === '' ? null
    : h('div', {class: 'kems-kv'}, h('span', {}, key), h('b', {}, String(value))));
  const pill = (tone, text) => h('span', {class: `kems-pill kems-pill-${tone}`}, text);
  const spinner = () => h('i', {class: 'kems-an-spin', 'aria-hidden': 'true'});
  const KIND_LABEL = {line: '선로', bus: '모선', transformer: '변압기', generator: '발전기', unknown: '설비'};

  /* ---------- per-grid API contracts ---------- */
  const enc = encodeURIComponent;
  const url = (path, params) => `${path}?${Object.entries(params).filter(([, value]) => value !== null && value !== undefined && value !== '')
    .map(([key, value]) => `${key}=${enc(value)}`).join('&')}`;
  const ADAPTERS = {
    'kr-kpg193': {
      securityUrl: (line) => url('/api/security-analysis', {outage_line_id: line, grid_id: 'kr-kpg193'}),
      sensitivityUrl: (line, monitored) => url('/api/sensitivity-analysis', {outage_line_id: line, monitored_line_id: monitored, top_n: TOP_N, grid_id: 'kr-kpg193'}),
      security: kpgSecurity,
      sensitivity: kpgSensitivity,
      notice: 'KPG-193 합성 시험계통 결과 · 실제 한국 운영계통 결과가 아닙니다.',
      generators: 'KPG-193 시험계통 모델 발전기 · 실제 발전소 운전 지시가 아닙니다.',
    },
    'mn-osm-35k': {
      securityUrl: (line) => url('/api/grids/mn-osm-35k/security-analysis', {outage_line_id: line}),
      sensitivityUrl: (line, monitored) => url('/api/grids/mn-osm-35k/sensitivity-analysis', {outage_line_id: line, monitored_line_id: monitored, top_n: TOP_N}),
      security: mnSecurity,
      sensitivity: mnSensitivity,
      notice: '합성 시나리오 · 실제 몽골 운영 데이터가 아닙니다.',
      generators: '합성 발전기 · 실제 몽골 발전소·재급전 후보가 아닙니다.',
    },
  };

  /** What may run for the grid: the catalog flag decides, and only a grid with an adapter is ever called. */
  function support(gridId, flags) {
    const adapter = ADAPTERS[gridId];
    const gate = (flag, note, name) => {
      if (!flags) return {ok: false, reason: 'EMS API 기능 상태 확인 전 · 실행하지 않습니다'};
      if (flags[flag] === false) return {ok: false, reason: `미지원 · EMS API ${name} = false${note ? ` · ${note}` : ''}`};
      if (flags[flag] !== true) return {ok: false, reason: `지원 여부가 EMS API 응답에 없습니다 (${name}) · 실행하지 않습니다`};
      if (!adapter) return {ok: false, reason: `이 콘솔에 ${gridId} 해석 API 연동이 없습니다 · 다른 계통 API로 대체하지 않습니다`};
      return {ok: true, reason: null};
    };
    return {
      security: gate('security', flags && flags.securityNote, 'security_analysis_available'),
      sensitivity: gate('sensitivity', flags && flags.sensitivityNote, 'sensitivity_analysis_available'),
    };
  }

  /* ---------- response validation ---------- */
  class Invalid extends Error {}
  const reject = (message) => { throw new Invalid(message); };
  const isObject = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
  const text = (value) => (typeof value === 'string' && value ? value : null);
  const number = (value, name) => {
    if (value === null || value === undefined) return null;
    if (!finite(value)) reject(`${name} 값이 유한한 숫자가 아닙니다`);
    return value;
  };
  const idList = (value, name) => {
    if (!Array.isArray(value)) reject(`${name} 목록이 없습니다`);
    return value.map(idOf);
  };
  const strings = (value) => (Array.isArray(value) ? value.filter((item) => typeof item === 'string' && item) : []);

  function violation(item, target) {
    if (!isObject(item)) reject('위반 항목 형식 오류');
    const id = idOf(item.equipment_id);
    if (!id) reject('위반 설비 ID가 없습니다');
    return {
      equipmentId: id, ...target(item, id),
      limitType: text(item.limit_type), limitName: text(item.limit_name), unit: text(item.unit),
      limit: number(item.limit, `위반 ${id} limit`), value: number(item.value, `위반 ${id} value`),
      amount: number(item.violation_amount, `위반 ${id} violation_amount`), direction: text(item.violation_direction),
      loading: number(item.loading_percent, `위반 ${id} loading_percent`),
    };
  }

  // Fields both backends return with the same meaning; everything else stays per grid.
  function securityCommon(payload, request, target) {
    if (!isObject(payload)) reject('응답 형식 오류 (객체 아님)');
    if (idOf(payload.outage_line_id) !== request.line) reject(`응답의 사고 선로(${idOf(payload.outage_line_id)})가 요청한 선로(${request.line})와 다릅니다`);
    if (typeof payload.base_converged !== 'boolean') reject('base_converged가 없습니다');
    if (typeof payload.violation_assessment_available !== 'boolean') reject('violation_assessment_available이 없습니다');
    if (typeof payload.analysis_status !== 'string' || !payload.analysis_status) reject('analysis_status가 없습니다');
    if (payload.post_status !== null && typeof payload.post_status !== 'string') reject('post_status 형식 오류');
    if (!Array.isArray(payload.violations)) reject('violations 목록이 없습니다');
    const assessed = payload.violation_assessment_available;
    // Violations are judged on a converged post-contingency state only: the three fields must agree.
    if (assessed !== (payload.post_status === 'CONVERGED' && payload.analysis_status === 'COMPLETED') || (assessed && !payload.base_converged)) {
      reject(`수렴 상태(base ${payload.base_converged} · post ${payload.post_status} · ${payload.analysis_status})와 위반 판정 가능 여부가 맞지 않습니다`);
    }
    let count = null;
    let violations = [];
    if (assessed) {
      count = payload.violated_equipment_count;
      if (!Number.isInteger(count) || count < 0) reject('violated_equipment_count 형식 오류');
      if (count > payload.violations.length || (count === 0) !== (payload.violations.length === 0)) {
        reject(`위반 설비 수(${count})와 위반 목록(${payload.violations.length}건)이 맞지 않습니다`);
      }
      violations = payload.violations.map((item) => violation(item, target));
    }
    return {
      kind: 'security', line: request.line, assessed, count, violations,
      verdict: !assessed ? 'not_assessed' : count > 0 ? 'violations' : 'secure',
      baseConverged: payload.base_converged, postStatus: payload.post_status, analysisStatus: payload.analysis_status,
      // Records reported for a non-converged state are never shown as violations.
      unjudged: assessed ? 0 : payload.violations.length,
    };
  }

  function kpgSecurity(payload, request, ctx) {
    const vm = securityCommon(payload, request, (item, id) => {
      if (ctx.hasLine(id)) return {type: 'line', mapKind: 'line', mapId: id};
      if (ctx.hasTransformer(id)) return {type: 'transformer', mapKind: 'transformer', mapId: id};
      // KPG voltage levels are "VL-<n>"; GET /api/grids/kr-kpg193/map numbers that level's bus <n> (_bus_number).
      const level = /^VL-(\d+)$/.exec(id);
      if (level && ctx.hasBus(level[1])) return {type: 'bus', mapKind: 'bus', mapId: level[1], alias: `전압 레벨 ${id} = 지도 모선 ${level[1]}`};
      return {type: 'unknown', mapKind: null, mapId: null};
    });
    const seen = new Set();
    const options = vm.violations.filter((v) => v.type === 'line' && !seen.has(v.equipmentId) && seen.add(v.equipmentId))
      .map((v) => ({line: v.equipmentId, note: `사고 후 위반${finite(v.loading) ? ` · 부하율 ${pct(v.loading)}` : ''}`}));
    // KPG has no pre-contingency status, islanding or post-contingency extremes in its response.
    return {...vm, gridId: 'kr-kpg193', preStatus: null, outage: ctx.lineEnds(request.line), outageSource: 'map',
      island: null, post: null, scenario: null, engine: null, limits: null, caveats: [], monitoredOptions: options};
  }

  const MN_TYPES = {Line: 'line', Bus: 'bus', Transformer: 'transformer', Generator: 'generator'};

  function mnSecurity(payload, request, ctx) {
    if (!isObject(payload) || payload.grid_id !== 'mn-osm-35k') reject(`응답의 계통 ID(${isObject(payload) ? idOf(payload.grid_id) : '?'})가 mn-osm-35k가 아닙니다`);
    const vm = securityCommon(payload, request, (item, id) => {
      const type = MN_TYPES[item.equipment_type] || 'unknown';
      const has = {line: ctx.hasLine, bus: ctx.hasBus, transformer: ctx.hasTransformer}[type];
      return has && has(id) ? {type, mapKind: type, mapId: id} : {type, mapKind: null, mapId: null};
    });
    if (typeof payload.pre_status !== 'string') reject('pre_status가 없습니다');
    const outage = payload.outage_line;
    if (!isObject(outage)) reject('outage_line이 없습니다');
    const ends = ctx.lineEnds(request.line);
    if (ends && (idOf(outage.bus0) !== ends.a || idOf(outage.bus1) !== ends.b)) {
      reject(`사고 선로 양단(${idOf(outage.bus0)}–${idOf(outage.bus1)})이 지도(${ends.a}–${ends.b})와 다릅니다`);
    }
    if (typeof payload.island_detected !== 'boolean') reject('island_detected가 없습니다');
    const isolated = idList(payload.isolated_buses, 'isolated_buses');
    if (payload.isolated_bus_count !== isolated.length || payload.island_detected !== (isolated.length > 0)) reject('분리 모선 수와 목록이 맞지 않습니다');
    const island = {
      detected: payload.island_detected, buses: isolated, loadMw: number(payload.isolated_load_mw, 'isolated_load_mw'),
      generators: idList(payload.isolated_generator_ids || [], 'isolated_generator_ids'),
      lines: idList(payload.isolated_line_ids || [], 'isolated_line_ids'),
      transformers: idList(payload.isolated_transformer_ids || [], 'isolated_transformer_ids'),
      pfComputed: payload.island_pf_computed === true,
      consistent: isObject(payload.island_check) && typeof payload.island_check.consistent === 'boolean' ? payload.island_check.consistent : null,
    };
    let post = null;
    if (vm.assessed) {
      const flows = Array.isArray(payload.top_line_flow_changes) ? payload.top_line_flow_changes : [];
      post = {
        vMin: number(payload.post_voltage_min_pu, 'post_voltage_min_pu'), vMax: number(payload.post_voltage_max_pu, 'post_voltage_max_pu'),
        maxLoading: {value: number(payload.post_max_line_loading_percent, 'post_max_line_loading_percent'), line: idOf(payload.post_max_line_loading_id)},
        maxDp: {value: number(payload.max_line_p_change_mw, 'max_line_p_change_mw'), line: idOf(payload.max_line_p_change_id)},
        maxDq: {value: number(payload.max_line_q_change_mvar, 'max_line_q_change_mvar'), line: idOf(payload.max_line_q_change_id)},
        maxDv: {value: number(payload.max_voltage_change_pu, 'max_voltage_change_pu'), bus: idOf(payload.max_voltage_change_bus_id)},
        flows: flows.map((item) => {
          if (!isObject(item) || !idOf(item.line_id)) reject('top_line_flow_changes 형식 오류');
          return {line: idOf(item.line_id), dp: number(item.dp_mw, 'dp_mw'), dq: number(item.dq_mvar, 'dq_mvar'), loading: number(item.post_loading_percent, 'post_loading_percent')};
        }),
        genQ: isObject(payload.generator_q_screening) ? {min: number(payload.generator_q_screening.min_mvar, 'min_mvar'),
          max: number(payload.generator_q_screening.max_mvar, 'max_mvar'), violations: idList(payload.generator_q_screening.violations || [], 'violations')} : null,
      };
    }
    return {...vm, gridId: 'mn-osm-35k', preStatus: payload.pre_status, outageSource: 'response',
      outage: {a: idOf(outage.bus0), b: idOf(outage.bus1), kv: number(outage.voltage_kv, 'outage_line.voltage_kv')},
      island, post, engine: text(payload.engine), limits: isObject(payload.screening_limits) ? payload.screening_limits : null,
      scenario: {id: text(payload.scenario_id), synthetic: payload.synthetic_scenario === true, actual: payload.actual_operating_data},
      caveats: strings(payload.model_caveats),
      monitoredOptions: post ? post.flows.map((flow) => ({line: flow.line, note: `사고 후 ΔP ${fixed(flow.dp, 3, 'MW')}`})) : []};
  }

  const SENSITIVITY_STATUS = {
    COMPLETED: {state: 'completed', label: '완료'},
    NO_OVERLOAD: {state: 'no_target', label: '감시 대상 없음 · 사고 후 과부하 선로 없음'},
    SECURITY_NOT_CONVERGED: {state: 'not_converged', label: '미평가 · 사고 후 PF 비수렴'},
    POST_CONTINGENCY_NOT_CONVERGED: {state: 'not_converged', label: '미평가 · 사고 후 PF 비수렴'},
    NOT_EVALUATED_ISLANDING: {state: 'not_evaluated', label: '미평가 · 계통 분리 사고'},
  };

  function sensitivityCommon(payload, request) {
    if (!isObject(payload)) reject('응답 형식 오류 (객체 아님)');
    if (idOf(payload.outage_line_id) !== request.line) reject(`응답의 사고 선로(${idOf(payload.outage_line_id)})가 요청한 선로(${request.line})와 다릅니다`);
    if (typeof payload.analysis_status !== 'string' || !payload.analysis_status) reject('analysis_status가 없습니다');
    if (!Array.isArray(payload.candidates)) reject('candidates 목록이 없습니다');
    if (payload.candidate_count !== payload.candidates.length) reject(`candidate_count(${payload.candidate_count})와 후보 목록(${payload.candidates.length}건)이 맞지 않습니다`);
    const known = SENSITIVITY_STATUS[payload.analysis_status] || {state: 'other', label: '미평가'};
    const completed = known.state === 'completed';
    const monitored = idOf(payload.monitored_line_id);
    if (completed) {
      if (!monitored) reject('완료 응답에 감시 선로가 없습니다');
      if (request.monitored && monitored !== request.monitored) reject(`응답의 감시 선로(${monitored})가 요청(${request.monitored})과 다릅니다`);
      if (monitored === request.line) reject('감시 선로가 사고 선로와 같습니다');
    } else if (payload.candidates.length) {
      reject(`${payload.analysis_status} 응답에 후보가 있습니다`);
    }
    return {kind: 'sensitivity', line: request.line, requested: request.monitored || null, analysisStatus: payload.analysis_status,
      state: known.state, label: known.label, completed, monitored: completed ? monitored : null, message: text(payload.message)};
  }

  function candidate(item, index, ctx, extra) {
    if (!isObject(item)) reject('후보 형식 오류');
    const id = idOf(item.generator_id);
    if (!id) reject('후보 발전기 ID가 없습니다');
    if (!finite(item.sensitivity)) reject(`후보 ${id}의 민감도가 유한한 숫자가 아닙니다`);
    if (item.abs_sensitivity !== undefined && (!finite(item.abs_sensitivity) || Math.abs(item.abs_sensitivity - Math.abs(item.sensitivity)) > 1e-12)) {
      reject(`후보 ${id}의 abs_sensitivity가 |sensitivity|와 다릅니다`);
    }
    const bus = idOf(item.bus_id);
    return {rank: index + 1, generator: id, value: item.sensitivity, bus: bus || null, mapBus: bus && ctx.hasBus(bus) ? bus : null, ...extra(item)};
  }

  // The API ranks by |sensitivity|; the console shows that order and never re-ranks it.
  function inOrder(candidates) {
    for (let i = 1; i < candidates.length; i++) {
      if (Math.abs(candidates[i].value) > Math.abs(candidates[i - 1].value)) reject('후보가 |민감도| 내림차순이 아닙니다');
    }
    return candidates;
  }

  function kpgSensitivity(payload, request, ctx) {
    const vm = sensitivityCommon(payload, request);
    const candidates = inOrder(payload.candidates.map((item, i) => candidate(item, i, ctx, (c) => ({name: text(c.name_korean)}))));
    return {...vm, gridId: 'kr-kpg193', candidates, selection: {mode: request.monitored ? 'user' : 'auto', rule: null}, alternatives: [],
      monitoredLine: vm.monitored ? ctx.lineEnds(vm.monitored) : null,
      // The KPG response has no unit field; its engine computes the same PyPowSyBl active-power branch flow factor.
      unit: {text: 'MW/MW', source: 'KPG 응답에 단위 필드 없음 · PyPowSyBl 유효전력 branch flow factor 정의'},
      definition: null, flows: null, counts: null, excluded: [], fd: null, caveats: [], isolated: null, postStatus: null};
  }

  function mnSensitivity(payload, request, ctx) {
    if (!isObject(payload) || payload.grid_id !== 'mn-osm-35k') reject(`응답의 계통 ID(${isObject(payload) ? idOf(payload.grid_id) : '?'})가 mn-osm-35k가 아닙니다`);
    const vm = sensitivityCommon(payload, request);
    if (payload.sensitivity_available !== vm.completed) reject('sensitivity_available과 analysis_status가 맞지 않습니다');
    const caveats = strings(payload.model_caveats);
    if (!vm.completed) {
      return {...vm, gridId: 'mn-osm-35k', candidates: [], selection: null, alternatives: [], monitoredLine: null, unit: null, definition: null,
        flows: null, counts: null, excluded: [], fd: null, caveats,
        isolated: payload.isolated_buses === undefined ? null : idList(payload.isolated_buses, 'isolated_buses'), postStatus: text(payload.post_status)};
    }
    const candidates = inOrder(payload.candidates.map((item, i) => {
      if (isObject(item) && item.rank !== i + 1) reject('후보 순위(rank)가 1부터 차례대로가 아닙니다');
      return candidate(item, i, ctx, (c) => ({kv: number(c.voltage_kv, 'voltage_kv'), control: text(c.control), target: number(c.target_p_mw, 'target_p_mw'),
        pre: number(c.pre_contingency_sensitivity, 'pre_contingency_sensitivity'), synthetic: c.synthetic_generator === true}));
    }));
    const fd = payload.finite_difference_check;
    if (!isObject(fd) || fd.passed !== true) reject('유한차분 검증을 통과하지 않은 응답입니다');
    const selection = isObject(payload.monitored_line_selection) ? payload.monitored_line_selection : {};
    if (selection.mode !== (request.monitored ? 'user' : 'auto')) reject(`감시 선로 선정 방식(${idOf(selection.mode)})이 요청과 다릅니다`);
    const monitored = payload.monitored_line;
    if (!isObject(monitored) || idOf(monitored.line_id) !== vm.monitored) reject('monitored_line이 감시 선로와 다릅니다');
    const ends = ctx.lineEnds(vm.monitored);
    if (ends && (idOf(monitored.bus0) !== ends.a || idOf(monitored.bus1) !== ends.b)) reject('감시 선로 양단이 지도와 다릅니다');
    return {...vm, gridId: 'mn-osm-35k', candidates, caveats, isolated: null, postStatus: null,
      selection: {mode: selection.mode, rule: text(selection.rule)},
      alternatives: (Array.isArray(selection.alternatives) ? selection.alternatives : []).filter(isObject)
        .map((alt) => ({line: idOf(alt.line_id), dp: number(alt.dp_mw, 'dp_mw'), radial: alt.radial_after_contingency === true})),
      monitoredLine: {a: idOf(monitored.bus0), b: idOf(monitored.bus1), kv: number(monitored.voltage_kv, 'voltage_kv'), rating: number(monitored.rating_mva, 'rating_mva'),
        radial: payload.monitored_line_radial_after_contingency === true, radialNote: text(payload.radial_note)},
      unit: {text: text(payload.unit) || '응답에 없음', source: 'EMS API 응답 unit'},
      definition: {fn: text(payload.function), variable: text(payload.variable), slack: text(payload.slack_balance)},
      flows: {base: number(payload.base_flow_mw, 'base_flow_mw'), post: number(payload.post_flow_mw, 'post_flow_mw'),
        loading: number(payload.post_loading_percent, 'post_loading_percent'), definition: text(payload.loading_definition)},
      counts: {evaluated: number(payload.evaluated_generator_count, 'evaluated_generator_count'), zero: number(payload.zero_sensitivity_count, 'zero_sensitivity_count')},
      excluded: (Array.isArray(payload.excluded_from_ranking) ? payload.excluded_from_ranking : []).filter(isObject)
        .map((item) => `${idOf(item.generator_id)}${text(item.reason) ? ` (${item.reason})` : ''}`),
      fd: {tolerance: text(fd.tolerance), method: text(fd.method), checked: Array.isArray(fd.results) ? fd.results.length : null}};
  }

  /** Validated view model of a response, or Invalid. ctx: map lookups of the grid on screen. */
  function security(gridId, payload, request, ctx) {
    const adapter = ADAPTERS[gridId];
    if (!adapter) reject(`${gridId}에는 해석 API 연동이 없습니다`);
    return adapter.security(payload, request, ctx);
  }

  function sensitivity(gridId, payload, request, ctx) {
    const adapter = ADAPTERS[gridId];
    if (!adapter) reject(`${gridId}에는 해석 API 연동이 없습니다`);
    return adapter.sensitivity(payload, request, ctx);
  }

  /* ---------- map overlay ---------- */
  function overlay(sec, sens) {
    if (!sec) return null;
    const mapped = (kind) => sec.violations.filter((v) => v.mapKind === kind).map((v) => v.mapId);
    const done = sens && sens.completed ? sens : null;
    return {
      outage: [sec.line],
      violationLines: mapped('line'), violationTransformers: mapped('transformer'), violationBuses: mapped('bus'),
      isolatedBuses: sec.island ? sec.island.buses : [], isolatedLines: sec.island ? sec.island.lines : [],
      monitored: done ? [done.monitored] : [],
      candidates: done ? done.candidates.filter((c) => c.mapBus).map((c) => ({bus: c.mapBus, rank: c.rank})) : [],
    };
  }

  /** Legend entries for the drawn overlay (`drawn` lists what the map actually drew). */
  function legend(drawn) {
    const has = (key) => drawn && drawn[key] && drawn[key].length > 0;
    return [
      h('span', {}, h('i', {class: 'kems-lgline an-outage'}), '사고 선로 (N-1)'),
      has('violationLines') ? h('span', {}, h('i', {class: 'kems-lgline an-viol'}), '사고 후 위반 선로') : null,
      has('violationBuses') || has('violationTransformers') ? h('span', {}, h('i', {class: 'kems-lgdot an-viol'}), '사고 후 위반 모선·변압기') : null,
      has('isolatedBuses') ? h('span', {}, h('i', {class: 'kems-lgdot an-iso'}), '분리 모선 (섬 PF 미계산)') : null,
      has('isolatedLines') ? h('span', {}, h('i', {class: 'kems-lgline an-iso'}), '분리 섬 선로') : null,
      has('monitored') ? h('span', {}, h('i', {class: 'kems-lgline an-mon'}), '감시 선로 (Sensitivity)') : null,
      has('candidates') ? h('span', {}, h('i', {class: 'kems-lgsq an-cand'}), '민감도 후보 모선 (순위)') : null,
      h('span', {class: 'kems-lgsrc'}, '색: 해석 응답에 있는 설비만 · 다른 선로의 사고 후 상태는 응답에 없어 칠하지 않음'),
    ];
  }

  /* ---------- display ---------- */
  function verdictText(vm) {
    if (vm.verdict === 'not_assessed') return vm.baseConverged ? `평가 불가 · 사고 후 PF ${vm.postStatus || '결과 없음'}` : '평가 불가 · 기준 PF 비수렴';
    return vm.verdict === 'violations' ? `위반 ${vm.count}건` : '위반 없음';
  }
  const verdictPill = (vm) => pill({violations: 'err', secure: 'ok', not_assessed: 'warn'}[vm.verdict], verdictText(vm));

  function limitText(v) {
    const digits = (value) => (finite(value) && Math.abs(value) >= 1000 ? 1 : 2);
    const low = v.direction === 'below_minimum';
    return [
      finite(v.limit) ? `${low ? '하한' : '한계'} ${fixed(v.limit, digits(v.limit), v.unit)}` : '한계 N/A',
      finite(v.value) ? `계산 ${fixed(v.value, digits(v.value), v.unit)}` : '계산 N/A',
      finite(v.loading) ? `부하율 ${pct(v.loading)}` : null,
      finite(v.amount) ? `${low ? '미달' : '초과'} ${fixed(v.amount, digits(v.amount), v.unit)}` : null,
    ].filter(Boolean).join(' · ');
  }

  const span = (ctx, ends) => (ends ? `${ctx.busLabel(ends.a)} — ${ctx.busLabel(ends.b)}${finite(ends.kv) ? ` · ${num(ends.kv, 0)} kV` : ''}` : '');

  /** Analysis block of the line card: the selected line as outage, its run button and status. */
  function lineBlock(info, line, act) {
    const a = info.analysis;
    const sec = a.security;
    const mine = a.line === line;
    const head = h('div', {class: 'kems-an-head'}, '선로 사고 해석 · N-1 Security Analysis');
    let reason = info.support.security.ok ? null : info.support.security.reason;
    if (!reason && info.excluded && info.excluded.has(line)) reason = 'PF 해석 제외 선로 · EMS API N-1 대상이 아닙니다 (pf-preview excluded_line_ids)';
    if (reason) {
      return h('div', {class: 'kems-an-block', 'data-an': 'unsupported'}, head,
        h('button', {type: 'button', class: 'btn kems-an-run', 'data-line': line, disabled: true, title: reason}, 'N-1 분석 실행'),
        h('p', {class: 'kems-an-stat'}, reason));
    }
    let stat = '이 선로를 사고 선로로 EMS API가 계산합니다 · 결과: 의사결정지원 패널';
    let tone = '';
    if (mine && sec.status === 'loading') stat = [spinner(), `${line} 사고 분석 중…`];
    else if (mine && sec.status === 'ready') {
      // A not-assessed verdict already names the post-contingency status.
      stat = `${verdictText(sec.vm)}${sec.vm.verdict === 'not_assessed' ? '' : ` · 사후 ${sec.vm.postStatus}`} · 상세: 의사결정지원 패널`;
      tone = {violations: ' kems-err', not_assessed: ' kems-warnt'}[sec.vm.verdict] || '';
    }
    else if (mine && sec.status === 'error') { stat = `N-1 실패 · ${sec.error}`; tone = ' kems-err'; }
    else if (mine && sec.status === 'invalid') { stat = `N-1 응답 검증 실패 · ${sec.error}`; tone = ' kems-err'; }
    else if (a.line && sec.status !== 'idle') stat = `표시 중인 결과: ${a.line} 사고 · 이 선로로 실행하면 이전 결과는 지웁니다`;
    return h('div', {class: 'kems-an-block', 'data-an': mine ? sec.status : 'idle'}, head,
      h('button', {type: 'button', class: 'btn pri kems-an-run', 'data-line': line, disabled: mine && sec.status === 'loading',
        onclick: () => act.runSecurity(line)}, mine && sec.status === 'ready' ? 'N-1 다시 실행' : 'N-1 분석 실행'),
      h('p', {class: `kems-an-stat${tone}`, role: 'status'}, stat));
  }

  /** Card rows that tie a map feature to the result on screen (never a value the response lacks). */
  function featureRows(info, kind, id) {
    const a = info.analysis;
    if (a.security.status !== 'ready') return [];
    const sec = a.security.vm;
    const rows = [];
    for (const v of sec.violations) {
      if (v.mapKind === kind && v.mapId === id) rows.push(row(`N-1 위반 (${sec.line} 사고)`, `${v.limitType || '위반'} · ${limitText(v)}`));
    }
    if (kind === 'bus' && sec.island && sec.island.buses.includes(id)) rows.push(row('N-1 계통 분리', `${sec.line} 사고 시 분리 모선 · 섬 PF 미계산`));
    if (kind === 'line' && sec.island && sec.island.lines.includes(id)) rows.push(row('N-1 계통 분리', `${sec.line} 사고 시 분리 섬 내부 선로`));
    if (kind === 'line' && sec.post) {
      const flow = sec.post.flows.find((item) => item.line === id);
      if (flow) rows.push(row(`사고 후 조류 (${sec.line} 사고)`, `ΔP ${fixed(flow.dp, 3, 'MW')} · ΔQ ${fixed(flow.dq, 3, 'Mvar')} · 부하율 ${pct(flow.loading)}`));
    }
    const sens = a.sensitivity.status === 'ready' && a.sensitivity.vm.completed ? a.sensitivity.vm : null;
    if (sens && kind === 'line' && sens.monitored === id) rows.push(row('Sensitivity 감시 선로', `${sens.line} 사고 후`));
    if (sens && kind === 'bus') {
      const here = sens.candidates.filter((c) => c.mapBus === id);
      if (here.length) rows.push(row('민감도 후보', here.map((c) => `${c.rank}. ${c.generator} ${num(c.value, 4)} ${sens.unit.text}`).join(', ')));
    }
    return rows;
  }

  const runButton = (label, attrs, onclick) => h('button', {type: 'button', class: 'btn pri kems-an-go', ...attrs, onclick}, label);
  const message = (tone, ...parts) => h('p', {class: `kems-msg${tone ? ` ${tone}` : ''}`}, ...parts);
  const pick = (act, kind, id, label, sub, value, tone) => h('button', {type: 'button', class: 'kems-rank', 'data-kind': kind, 'data-id': id,
    onclick: () => act.select(kind, id)}, h('span', {}, h('b', {}, label), sub ? h('em', {}, sub) : null), value ? h('i', {class: tone || ''}, value) : null);

  function securitySection(info, act) {
    const a = info.analysis;
    const sec = a.security;
    const head = h('h3', {}, 'N-1 Security Analysis · 선로 사고');
    const wrap = (...body) => h('section', {class: 'kems-sec kems-an', 'data-sec': 'n1', 'data-status': info.support.security.ok ? sec.status : 'unsupported'}, head, ...body);
    if (!info.support.security.ok) return wrap(message('', info.support.security.reason, ' · 분석 API를 호출하지 않습니다.'));
    if (sec.status === 'idle') return wrap(message('', '지도에서 선로를 선택하고 상세 카드의 [N-1 분석 실행]을 누르세요. EMS API가 계산하며 Artifact 데모 엔진은 쓰지 않습니다.'));
    if (sec.status === 'loading') return wrap(message('kems-an-loading', spinner(), `${a.line} 사고 분석 중… (EMS API)`));
    if (sec.status === 'error') return wrap(message('kems-err', `N-1 분석 실패 (${a.line}) · ${sec.error} · 데모 결과로 대체하지 않습니다.`), clearButton(act));
    if (sec.status === 'invalid') return wrap(message('kems-err', `N-1 응답 검증 실패 (${a.line}) · ${sec.error} · 결과를 표시하지 않습니다.`), clearButton(act));
    const vm = sec.vm;
    const ctx = info.ctx;
    const badges = [verdictPill(vm),
      vm.island && vm.island.detected ? pill('warn', `계통 분리 · 모선 ${vm.island.buses.length}`) : null,
      vm.scenario && vm.scenario.synthetic ? pill('loading', '합성 시나리오') : null,
      vm.scenario && vm.scenario.actual === false ? pill('loading', '실운영 데이터 아님') : null];
    const facts = [
      row('사고 선로', `${vm.line}${vm.outage ? ` · ${span(ctx, vm.outage)}` : ''}`),
      row('사전 (기준) PF', vm.preStatus ? `${vm.preStatus}${vm.baseConverged ? '' : ' · 기준 PF 비수렴'}` : vm.baseConverged ? '수렴 (base_converged)' : '비수렴 (base_converged = false)'),
      row('사후 PF', vm.postStatus || '결과 없음'),
      row('해석 상태', vm.analysisStatus),
      row('위반 설비 수', vm.assessed ? `${vm.count}건${vm.island && vm.island.detected ? ' (주 계통 기준)' : ''}` : '평가 불가 (사고 후 비수렴 · 0건으로 보지 않음)'),
    ];
    if (vm.island) {
      facts.push(row('계통 분리', vm.island.detected ? `있음 · 분리 모선 ${vm.island.buses.length} · 분리 부하 ${fixed(vm.island.loadMw, 2, 'MW')}` : '없음'));
    } else {
      facts.push(row('계통 분리', 'KPG 응답에 없음'));
    }
    if (vm.post) {
      const p = vm.post;
      facts.push(row('사고 후 최대 선로 부하율', `${pct(p.maxLoading.value)}${p.maxLoading.line ? ` (${p.maxLoading.line})` : ''}`),
        row('최대 선로 P 변화', `${fixed(p.maxDp.value, 3, 'MW')}${p.maxDp.line ? ` (${p.maxDp.line})` : ''}`),
        row('최대 선로 Q 변화', `${fixed(p.maxDq.value, 3, 'Mvar')}${p.maxDq.line ? ` (${p.maxDq.line})` : ''}`),
        row('사고 후 전압 범위', `${fixed(p.vMin, 4)} – ${fixed(p.vMax, 4)} pu`),
        row('최대 전압 변화', `${fixed(p.maxDv.value, 4, 'pu')}${p.maxDv.bus ? ` (모선 ${p.maxDv.bus})` : ''}`));
      if (p.genQ) facts.push(row('발전기 Q 범위', `${fixed(p.genQ.min, 3)} – ${fixed(p.genQ.max, 3)} Mvar${p.genQ.violations.length ? ` · 위반 ${p.genQ.violations.join(', ')}` : ''}`));
    }
    const parts = [h('div', {class: 'kems-badges'}, badges), h('div', {class: 'kems-facts'}, facts)];
    if (vm.unjudged) parts.push(message('kems-warnt', `비수렴 상태에서 보고된 위반 기록 ${vm.unjudged}건은 판정에 쓰지 않았습니다.`));
    if (vm.violations.length) {
      parts.push(h('h4', {class: 'kems-an-h4'}, `위반 설비 · 한계값과 사고 후 계산값 (${vm.violations.length}건)`),
        h('div', {class: 'kems-an-list', 'data-list': 'violations'}, vm.violations.map((v) => (v.mapKind
          ? pick(act, v.mapKind, v.mapId, `${KIND_LABEL[v.type]} ${v.equipmentId}${v.alias ? ` (모선 ${v.mapId})` : ''}`,
            `${v.limitType || ''}${v.limitName ? ` · ${v.limitName}` : ''}`, limitText(v), 'kems-err')
          : h('div', {class: 'kems-rank kems-an-nomap', 'data-id': v.equipmentId}, h('span', {}, h('b', {}, `${KIND_LABEL[v.type]} ${v.equipmentId}`),
            h('em', {}, `${v.limitType || ''} · 지도 연계 없음`)), h('i', {class: 'kems-err'}, limitText(v)))))));
    } else if (vm.assessed) {
      parts.push(message('', `사고 후 위반 설비 없음${vm.island && vm.island.detected ? ' (분리 섬은 PF를 계산하지 않아 판정 범위 밖)' : ''}`));
    }
    if (vm.island && vm.island.detected) {
      const i = vm.island;
      parts.push(h('h4', {class: 'kems-an-h4'}, '계통 분리 · 주 계통만 계산'), h('div', {class: 'kems-facts', 'data-list': 'island'},
        row('분리 모선', i.buses.join(' ')), row('분리 합성 부하', fixed(i.loadMw, 2, 'MW')),
        row('분리 합성 발전기', i.generators.length ? i.generators.join(' ') : '없음'),
        row('섬 내부 선로·변압기', `${i.lines.length}개 · ${i.transformers.length}개`),
        row('분리 섬 PF', i.pfComputed ? '계산됨' : '미실행 (전압·조류 결과 없음)'),
        row('토폴로지 교차검증', i.consistent === true ? '일치' : i.consistent === false ? '불일치' : null)));
    }
    if (vm.post && vm.post.flows.length) {
      parts.push(h('h4', {class: 'kems-an-h4'}, '선로 조류 변화 상위 · 사고 후 (EMS API 상위 목록)'),
        h('div', {class: 'kems-an-list', 'data-list': 'flows'}, vm.post.flows.map((flow) => pick(act, 'line', flow.line, flow.line,
          `ΔQ ${fixed(flow.dq, 3, 'Mvar')} · 사고 후 부하율 ${pct(flow.loading)}`, `ΔP ${fixed(flow.dp, 3, 'MW')}`))));
    }
    const limits = vm.limits;
    if (limits || vm.caveats.length || vm.engine) {
      parts.push(h('details', {class: 'kems-an-more'}, h('summary', {}, '스크리닝 기준 · 모델 주의사항'), h('div', {class: 'kems-facts'},
        row('엔진', vm.engine), row('시나리오', vm.scenario && vm.scenario.id),
        row('전압 기준', limits && Array.isArray(limits.voltage_pu) ? `${limits.voltage_pu.join(' – ')} pu` : null),
        row('열적 기준', limits && typeof limits.thermal === 'string' ? limits.thermal : null),
        row('발전기 Q 기준', limits && Array.isArray(limits.generator_q_mvar) ? `${limits.generator_q_mvar.join(' – ')} Mvar` : null)),
      vm.caveats.length ? h('ul', {class: 'kems-an-ul'}, vm.caveats.map((item) => h('li', {}, item))) : null));
    }
    parts.push(h('p', {class: 'kems-sub'}, `${ADAPTERS[vm.gridId].notice} · EMS API 계산 결과 (조회 시각 ${info.clock(sec.at)})`), clearButton(act));
    return wrap(...parts);
  }

  const clearButton = (act) => h('div', {class: 'kems-an-actions'}, h('button', {type: 'button', class: 'btn kems-an-clear', onclick: () => act.clear()}, '해석 결과 지우기'));

  function monitoredSelect(info, act) {
    const a = info.analysis;
    const sens = a.sensitivity.status === 'ready' ? a.sensitivity.vm : null;
    const options = new Map([['', 'EMS API 자동 선정']]);
    for (const item of a.security.vm.monitoredOptions) options.set(item.line, `${item.line} · ${item.note}`);
    if (sens) for (const alt of sens.alternatives) if (!options.has(alt.line)) options.set(alt.line, `${alt.line} · 대안 (ΔP ${fixed(alt.dp, 3, 'MW')}${alt.radial ? ' · 방사형' : ''})`);
    if (a.monitored && !options.has(a.monitored)) options.set(a.monitored, a.monitored);
    const select = h('select', {class: 'kems-an-mon', 'aria-label': '감시 선로', onchange: (event) => act.setMonitored(event.target.value)},
      [...options].map(([value, label]) => h('option', {value}, label)));
    select.value = a.monitored || '';
    return h('label', {class: 'kems-an-field'}, h('span', {}, '감시 선로'), select);
  }

  function sensitivitySection(info, act) {
    const a = info.analysis;
    const sens = a.sensitivity;
    const head = h('h3', {}, 'Sensitivity Analysis · 사고 후 선로 조류 민감도');
    const gate = info.support.sensitivity;
    const wrap = (...body) => h('section', {class: 'kems-sec kems-an', 'data-sec': 'sens', 'data-status': gate.ok ? sens.status : 'unsupported'}, head, ...body);
    if (!gate.ok) return wrap(message('', gate.reason, ' · 분석 API를 호출하지 않습니다.'));
    if (a.security.status !== 'ready') {
      return wrap(message('', 'N-1 결과가 나온 뒤 같은 사고 선로로 실행할 수 있습니다.'),
        runButton('Sensitivity 실행', {disabled: true, 'data-action': 'sensitivity'}, null));
    }
    const parts = [h('div', {class: 'kems-an-controls'}, monitoredSelect(info, act),
      runButton(sens.status === 'ready' ? 'Sensitivity 다시 실행' : 'Sensitivity 실행', {disabled: sens.status === 'loading', 'data-action': 'sensitivity'}, () => act.runSensitivity())),
    h('p', {class: 'kems-sub'}, `사고 선로 ${a.line} · 감시 선로를 비우면 EMS API가 선정합니다 · 바꾸면 이전 Sensitivity 결과를 지웁니다 · 상위 ${TOP_N}개 후보`)];
    if (sens.status === 'loading') parts.push(message('kems-an-loading', spinner(), `Sensitivity 계산 중… (감시 선로 ${sens.requested || 'EMS API 자동 선정'})`));
    else if (sens.status === 'error') parts.push(message('kems-err', `Sensitivity 실패 · ${sens.error} · 데모 결과로 대체하지 않습니다.`));
    else if (sens.status === 'invalid') parts.push(message('kems-err', `Sensitivity 응답 검증 실패 · ${sens.error} · 결과를 표시하지 않습니다.`));
    else if (sens.status === 'ready') parts.push(...sensitivityResult(sens.vm, info, act));
    return wrap(...parts);
  }

  function sensitivityResult(vm, info, act) {
    const ctx = info.ctx;
    const badges = h('div', {class: 'kems-badges'}, pill(vm.completed ? 'ok' : vm.state === 'no_target' ? 'loading' : 'warn', vm.completed ? '계산 완료' : vm.label),
      pill('loading', '소신호 민감도 · 재급전 실행 아님'));
    const facts = [row('상태', `${vm.label} (${vm.analysisStatus})`), row('사고 선로', vm.line)];
    if (!vm.completed) {
      facts.push(row('메시지', vm.message), row('감시 선로', vm.requested ? `${vm.requested} (요청)` : '선정되지 않음'),
        row('분리 모선', vm.isolated && vm.isolated.length ? vm.isolated.join(' ') : null), row('사고 후 PF', vm.postStatus));
      return [badges, h('div', {class: 'kems-facts'}, facts), message('', '후보 순위를 표시하지 않습니다 (정상 완료가 아님).'),
        vm.caveats.length ? h('details', {class: 'kems-an-more'}, h('summary', {}, '모델 주의사항'), h('ul', {class: 'kems-an-ul'}, vm.caveats.map((item) => h('li', {}, item)))) : null];
    }
    facts.push(row('감시 선로', `${vm.monitored}${vm.monitoredLine ? ` · ${span(ctx, vm.monitoredLine)}` : ''}`),
      row('선정', vm.selection.mode === 'auto' ? `EMS API 자동 선정${vm.selection.rule ? ` · ${vm.selection.rule}` : ''}` : '사용자 선택'),
      row('단위', `${vm.unit.text} (${vm.unit.source})`));
    const unit = vm.unit.text;
    const table = h('table', {class: 'reg kems-an-cand', 'data-list': 'candidates'},
      h('tr', {}, h('th', {}, '순위'), h('th', {}, '발전기'), h('th', {}, `민감도 (${unit})`), h('th', {}, '연계 모선')),
      vm.candidates.map((c) => h('tr', {'data-generator': c.generator, 'data-rank': c.rank},
        h('td', {}, String(c.rank)),
        h('td', {}, h('code', {}, c.generator), c.control || finite(c.target) ? h('div', {class: 'd'}, [c.control, finite(c.target) ? `출력 ${fixed(c.target, 3, 'MW')}` : null].filter(Boolean).join(' · ')) : null),
        h('td', {class: 'kems-an-val'}, h('b', {}, num(c.value, 4)), h('div', {class: 'd'}, c.value > 0 ? '+ 출력 증가 시 조류 증가' : c.value < 0 ? '− 출력 증가 시 조류 감소' : '0 · 영향 없음')),
        h('td', {}, c.mapBus ? h('button', {type: 'button', class: 'kems-an-bus', onclick: () => act.select('bus', c.mapBus)}, ctx.busLabel(c.mapBus))
          : h('span', {class: 'd'}, c.bus ? `모선 ${c.bus} · 지도 연계 없음` : '지도 연계 없음')))));
    const meaning = h('ul', {class: 'kems-an-ul', 'data-list': 'meaning'},
      h('li', {}, `부호: + 는 그 발전기 출력(주입)을 늘리면 감시 선로 유효전력 조류가 늘고, − 는 줄어듭니다 (${unit}).`),
      h('li', {}, '순위는 |민감도| 크기 순이며 조치 효과·조정 가능량·비용을 뜻하지 않습니다.'),
      h('li', {}, '사고 후 운전점의 소신호(선형화) 민감도입니다. 재급전을 실행하거나 권고하지 않으며 실제 제어는 수행되지 않았습니다.'),
      h('li', {}, ADAPTERS[vm.gridId].generators));
    const parts = [badges, h('div', {class: 'kems-facts'}, facts), table, meaning];
    if (vm.definition) {
      const f = vm.flows;
      parts.push(h('div', {class: 'kems-facts'},
        row('함수', vm.definition.fn), row('변수', vm.definition.variable), row('유효전력 균형', vm.definition.slack),
        row('감시 선로 조류 (사고 전 → 후)', f ? `${fixed(f.base, 3)} → ${fixed(f.post, 3, 'MW')}` : null),
        row('감시 선로 사고 후 부하율', f && finite(f.loading) ? `${pct(f.loading)}${f.definition ? ` · ${f.definition}` : ''}` : null),
        row('사고 후 방사형', vm.monitoredLine.radial ? `예 · ${vm.monitoredLine.radialNote || ''}` : '아니오'),
        row('평가 발전기', vm.counts && finite(vm.counts.evaluated) ? `${vm.counts.evaluated}대 · 민감도 0 ${vm.counts.zero}대` : null),
        row('순위 제외', vm.excluded.join(', ') || null),
        row('유한차분 검증', vm.fd ? `통과 · ${vm.fd.checked ?? 'N/A'}대 · ${vm.fd.tolerance || ''}` : null)));
    }
    if (vm.alternatives.length) {
      parts.push(h('h4', {class: 'kems-an-h4'}, '대안 감시 선로 · 누르면 감시 선로로 지정하고 이 결과는 지웁니다 (실행은 버튼으로)'),
        h('div', {class: 'kems-an-list', 'data-list': 'alternatives'}, vm.alternatives.map((alt) => h('button', {type: 'button', class: 'kems-rank', 'data-line': alt.line,
          onclick: () => act.setMonitored(alt.line)}, h('span', {}, h('b', {}, alt.line), h('em', {}, alt.radial ? '사고 후 방사형' : '사고 후 메시')),
          h('i', {}, `ΔP ${fixed(alt.dp, 3, 'MW')}`)))));
    }
    if (vm.caveats.length) parts.push(h('details', {class: 'kems-an-more'}, h('summary', {}, '모델 주의사항'), h('ul', {class: 'kems-an-ul'}, vm.caveats.map((item) => h('li', {}, item)))));
    parts.push(h('p', {class: 'kems-sub'}, `EMS API 계산 결과 (조회 시각 ${info.clock(info.analysis.sensitivity.at)})`));
    return parts;
  }

  /** The 의사결정지원 sections for the grid on screen. */
  function panel(info, act) {
    return [securitySection(info, act), sensitivitySection(info, act)];
  }

  return Object.freeze({TIMEOUT_MS, Invalid, adapter: (gridId) => ADAPTERS[gridId] || null, support, security, sensitivity, overlay, legend,
    lineBlock, featureRows, panel});
})();
