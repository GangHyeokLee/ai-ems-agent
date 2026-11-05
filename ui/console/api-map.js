/* K-EMS console: read-only map of an EMS API grid (GET /api/grids/{id}/map).
 *
 * The bridge (api-bridge.js) hands over a normalized model: buses, lines (with their display
 * parts: OSM routes, display geometry or a straight bus-to-bus segment), transformers, plants and
 * the geography rings, all in [longitude, latitude]. Nothing here computes power flow.
 *
 * Performance: every layer is a few combined <path> elements (one per style class), so even the
 * Korean OSM grid (~4k buses, ~4k lines, ~50k route vertices) stays a handful of DOM nodes. Zoom
 * and pan only change the viewBox; markers and strokes keep their pixel size through
 * vector-effect: non-scaling-stroke (a bus is a zero-length subpath with a round cap). Selection
 * is a hit test in script against the projected coordinates; listeners are attached once when the
 * map is created, never per grid or per element.
 *
 * Phase 3: overlay(spec) draws an analysis result above the lines (outage line, violated equipment,
 * islands, monitored line, ranked candidate buses) from IDs the bridge got from the analysis
 * response. It only highlights those features; the line colours underneath stay as painted.
 */
const KemsApiMap = (() => {
  'use strict';

  const NS = 'http://www.w3.org/2000/svg';
  const WIDTH = 1000;
  const PAD = 18;
  const LABEL_LIMIT = 40;
  const r1 = (n) => Math.round(n * 10) / 10;

  function el(tag, attrs) {
    const node = document.createElementNS(NS, tag);
    for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, value);
    return node;
  }

  // Equirectangular projection with the longitude scaled by cos(mean latitude), as in ui/index.html.
  function projection(points) {
    let minLon = Infinity, maxLon = -Infinity, minLat = Infinity, maxLat = -Infinity;
    for (const [lon, lat] of points) {
      if (lon < minLon) minLon = lon; if (lon > maxLon) maxLon = lon;
      if (lat < minLat) minLat = lat; if (lat > maxLat) maxLat = lat;
    }
    const k = Math.cos(((minLat + maxLat) / 2) * Math.PI / 180);
    const geoW = (maxLon - minLon) * k || 1;
    const geoH = (maxLat - minLat) || 1;
    const scale = (WIDTH - 2 * PAD) / geoW;
    return {
      width: WIDTH,
      height: Math.max(200, geoH * scale + 2 * PAD),
      project: (lon, lat) => [PAD + (lon - minLon) * k * scale, PAD + (maxLat - lat) * scale],
    };
  }

  function ringsPath(polygons, project) {
    let d = '';
    for (const polygon of polygons) {
      for (const ring of polygon) {
        if (!Array.isArray(ring) || ring.length < 3) continue;
        ring.forEach(([lon, lat], i) => { const [x, y] = project(lon, lat); d += `${i ? 'L' : 'M'}${r1(x)} ${r1(y)}`; });
        d += 'Z';
      }
    }
    return d;
  }

  function partsPath(parts) {
    let d = '';
    for (const part of parts) part.forEach(([x, y], i) => { d += `${i ? 'L' : 'M'}${r1(x)} ${r1(y)}`; });
    return d;
  }

  const dot = ([x, y]) => `M${r1(x)} ${r1(y)}h0`;

  function segmentDistance(px, py, ax, ay, bx, by) {
    const dx = bx - ax, dy = by - ay;
    const len = dx * dx + dy * dy;
    const t = len ? Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / len)) : 0;
    return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
  }

  function create(host, {onSelect} = {}) {
    const svg = el('svg', {class: 'kems-svg', role: 'img', 'aria-label': '계통 지도', preserveAspectRatio: 'xMidYMid meet'});
    const layers = {};
    for (const name of ['geo', 'lines', 'trafos', 'buses', 'plants', 'alerts', 'analysis', 'labels', 'sel']) {
      layers[name] = el('g', {class: `k-${name}`});
      svg.append(layers[name]);
    }
    host.append(svg);

    let model = null;   // projected model of the grid on screen
    let base = null;    // full extent {x, y, w, h}
    let vb = null;
    let selected = null;
    let lineClass = () => 'k-v-unk';
    let busAlert = () => false;
    let overlaySpec = null;  // analysis overlay requested by the bridge
    let drawn = null;        // the IDs of that overlay actually present on this map

    const setViewBox = () => svg.setAttribute('viewBox', `${vb.x} ${vb.y} ${vb.w} ${vb.h}`);
    const scale = () => { const m = svg.getScreenCTM(); return m ? m.a : 1; };
    const toUser = (clientX, clientY) => {
      const m = svg.getScreenCTM();
      if (!m) return null;
      const p = new DOMPoint(clientX, clientY).matrixTransform(m.inverse());
      return [p.x, p.y];
    };

    function zoomAt(factor, cx, cy) {
      if (!vb) return;
      const w = Math.min(base.w * 1.6, Math.max(base.w / 60, vb.w * factor));
      const k = w / vb.w;
      const x = cx ?? vb.x + vb.w / 2;
      const y = cy ?? vb.y + vb.h / 2;
      vb = {x: x - (x - vb.x) * k, y: y - (y - vb.y) * k, w, h: vb.h * k};
      setViewBox();
      updateLabels();
    }

    function updateLabels() {
      const size = 11 / scale();
      for (const text of layers.labels.children) text.setAttribute('font-size', size.toFixed(3));
      for (const text of layers.analysis.querySelectorAll('text')) text.setAttribute('font-size', (size * 1.15).toFixed(3));
    }

    /* ---------- pointer: wheel zoom, drag pan, click select (attached once) ---------- */
    let drag = null;
    svg.addEventListener('wheel', (event) => {
      if (!vb) return;
      event.preventDefault();
      const p = toUser(event.clientX, event.clientY);
      zoomAt(Math.exp(event.deltaY * 0.0015), p && p[0], p && p[1]);
    }, {passive: false});
    svg.addEventListener('pointerdown', (event) => {
      if (!vb || event.button !== 0) return;
      drag = {x: event.clientX, y: event.clientY, vb: {...vb}, moved: false, id: event.pointerId};
    });
    svg.addEventListener('pointermove', (event) => {
      if (!drag || event.pointerId !== drag.id) return;
      const dx = event.clientX - drag.x, dy = event.clientY - drag.y;
      if (!drag.moved && Math.abs(dx) + Math.abs(dy) > 4) {
        drag.moved = true;
        try { svg.setPointerCapture(event.pointerId); } catch (error) { /* pointer already released */ }
        svg.classList.add('dragging');
      }
      if (!drag.moved) return;
      const s = scale();
      vb = {...drag.vb, x: drag.vb.x - dx / s, y: drag.vb.y - dy / s};
      setViewBox();
    });
    const endDrag = (event) => {
      if (!drag || event.pointerId !== drag.id) return;
      const click = !drag.moved && event.type === 'pointerup';
      drag = null;
      svg.classList.remove('dragging');
      if (click) {
        const hit = hitTest(event.clientX, event.clientY);
        select(hit, false);
      }
    };
    svg.addEventListener('pointerup', endDrag);
    svg.addEventListener('pointercancel', endDrag);

    /* ---------- hit test against projected coordinates ---------- */
    function hitTest(clientX, clientY, tolerancePx = 8) {
      if (!model) return null;
      const p = toUser(clientX, clientY);
      if (!p) return null;
      const tol = tolerancePx / scale();
      let best = null;
      const consider = (kind, item, d) => { if (d <= tol && (!best || d < best.d)) best = {kind, item, d}; };
      for (const bus of model.buses) consider('bus', bus, Math.hypot(p[0] - bus.xy[0], p[1] - bus.xy[1]));
      for (const plant of model.plants) consider('plant', plant, Math.hypot(p[0] - plant.xy[0], p[1] - plant.xy[1]));
      for (const trafo of model.transformers) consider('transformer', trafo, Math.hypot(p[0] - trafo.mid[0], p[1] - trafo.mid[1]));
      if (best) return {kind: best.kind, item: best.item};
      for (const line of model.lines) {
        for (const part of line.parts) {
          for (let i = 1; i < part.length; i++) {
            consider('line', line, segmentDistance(p[0], p[1], part[i - 1][0], part[i - 1][1], part[i][0], part[i][1]));
          }
        }
      }
      return best ? {kind: best.kind, item: best.item} : null;
    }

    /* ---------- drawing ---------- */
    function clear() {
      for (const layer of Object.values(layers)) layer.replaceChildren();
      svg.removeAttribute('viewBox');
      model = null;
      base = vb = null;
      selected = null;
      lineClass = () => 'k-v-unk';
      busAlert = () => false;
      overlaySpec = drawn = null;
      host.dataset.kemsMap = '';
    }

    function show(input) {
      clear();
      const points = [];
      for (const polygon of input.country) for (const ring of polygon) for (const pt of ring) points.push(pt);
      if (!points.length) {
        for (const bus of input.buses) points.push([bus.lon, bus.lat]);
        for (const plant of input.plants) points.push([plant.lon, plant.lat]);
      }
      const proj = projection(points);
      const P = (lon, lat) => proj.project(lon, lat);
      const buses = input.buses.map((bus) => ({...bus, xy: P(bus.lon, bus.lat)}));
      const plants = input.plants.map((plant) => ({...plant, xy: P(plant.lon, plant.lat)}));
      const lines = input.lines.map((line) => {
        const parts = line.parts.map((part) => part.map(([lon, lat]) => P(lon, lat)));
        return {...line, parts, d: partsPath(parts)};
      });
      const transformers = input.transformers.map((trafo) => {
        const a = P(trafo.from.lon, trafo.from.lat), b = P(trafo.to.lon, trafo.to.lat);
        return {...trafo, a, b, mid: [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]};
      });
      model = {buses, plants, lines, transformers, busById: new Map(buses.map((bus) => [bus.id, bus])),
        lineById: new Map(lines.map((line) => [line.id, line])), plantById: new Map(plants.map((plant) => [plant.id, plant])),
        trafoById: new Map(transformers.map((trafo) => [trafo.id, trafo]))};

      const country = ringsPath(input.country, P);
      if (country) layers.geo.append(el('path', {class: 'k-land', d: country, 'fill-rule': 'evenodd'}));
      const regions = ringsPath(input.regions.flatMap((region) => region.polygons), P);
      if (regions) layers.geo.append(el('path', {class: 'k-region', d: regions}));

      let trafoLinks = '', trafoMarks = '';
      for (const trafo of transformers) {
        if (Math.hypot(trafo.a[0] - trafo.b[0], trafo.a[1] - trafo.b[1]) > 0.5) trafoLinks += partsPath([[trafo.a, trafo.b]]);
        trafoMarks += dot(trafo.mid);
      }
      if (trafoLinks) layers.trafos.append(el('path', {class: 'k-trafo-link', d: trafoLinks}));
      if (trafoMarks) layers.trafos.append(el('path', {class: 'k-trafo', d: trafoMarks}));

      const groups = {};
      for (const bus of buses) {
        const key = `${bus.cls} ${bus.feeds ? 'k-feed' : 'k-plain'}`;
        (groups[key] = groups[key] || []).push(dot(bus.xy));
      }
      for (const [key, dots] of Object.entries(groups)) {
        const d = dots.join('');
        if (key.endsWith('k-plain')) {
          layers.buses.append(el('path', {class: `k-bus ${key} k-ring`, d}));
          layers.buses.append(el('path', {class: `k-bus ${key} k-hole`, d}));
        } else {
          layers.buses.append(el('path', {class: `k-bus ${key}`, d}));
        }
      }
      if (plants.length) layers.plants.append(el('path', {class: 'k-plant', d: plants.map((plant) => dot(plant.xy)).join('')}));

      // Labels only where names are meaningful (not the IDs themselves), for the top voltage class.
      const named = buses.filter((bus) => bus.cls === 'k-v1' && bus.name && bus.name !== bus.id);
      if (named.length && named.length <= LABEL_LIMIT) {
        for (const bus of named) {
          const text = el('text', {x: r1(bus.xy[0] + 3), y: r1(bus.xy[1] - 3), class: 'k-label'});
          text.textContent = bus.name;
          layers.labels.append(text);
        }
      }

      base = {x: 0, y: 0, w: proj.width, h: proj.height};
      vb = {...base};
      setViewBox();
      paintLines();
      updateLabels();
      host.dataset.kemsMap = 'ready';
    }

    function paintLines() {
      layers.lines.replaceChildren();
      layers.alerts.replaceChildren();
      if (!model) return;
      const groups = {};
      for (const line of model.lines) {
        const key = lineClass(line);
        (groups[key] = groups[key] || []).push(line.d);
      }
      for (const [key, ds] of Object.entries(groups)) layers.lines.append(el('path', {class: `k-line ${key}`, d: ds.join('')}));
      const alerts = model.buses.filter((bus) => busAlert(bus));
      if (alerts.length) layers.alerts.append(el('path', {class: 'k-alert', d: alerts.map((bus) => dot(bus.xy)).join('')}));
      drawSelection();
    }

    /** classify(line) -> class names; alert(bus) -> boolean. Only regroups paths; no data change. */
    function paint(classify, alert) {
      lineClass = classify || (() => 'k-v-unk');
      busAlert = alert || (() => false);
      paintLines();
    }

    /* ---------- analysis overlay (IDs from the analysis response; features absent here are skipped) ---------- */
    function midpoint(line) {
      const part = line.parts[0];
      const a = part[Math.floor((part.length - 1) / 2)], b = part[Math.floor((part.length - 1) / 2) + 1] || a;
      return [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
    }

    function drawOverlay() {
      layers.analysis.replaceChildren();
      drawn = null;
      if (!model || !overlaySpec) return;
      const found = (table, ids) => [...new Set((ids || []).map(String))].map((id) => table.get(id)).filter(Boolean);
      const out = {};
      const add = (key, cls, items, d) => {
        out[key] = items.map((item) => item.id);
        if (items.length) layers.analysis.append(el('path', {class: `k-an ${cls}`, d: items.map(d).join('')}));
      };
      // Bottom to top: islands, monitored line, violations, then the outage line and the candidate ranks.
      add('isolatedLines', 'k-an-isoline', found(model.lineById, overlaySpec.isolatedLines), (line) => line.d);
      add('isolatedBuses', 'k-an-isobus', found(model.busById, overlaySpec.isolatedBuses), (bus) => dot(bus.xy));
      add('monitored', 'k-an-mon', found(model.lineById, overlaySpec.monitored), (line) => line.d);
      add('violationLines', 'k-an-viol', found(model.lineById, overlaySpec.violationLines), (line) => line.d);
      add('violationTransformers', 'k-an-violdot', found(model.trafoById, overlaySpec.violationTransformers), (trafo) => dot(trafo.mid));
      add('violationBuses', 'k-an-violdot', found(model.busById, overlaySpec.violationBuses), (bus) => dot(bus.xy));
      const outage = found(model.lineById, overlaySpec.outage);
      out.outage = outage.map((line) => line.id);
      if (outage.length) {
        const d = outage.map((line) => line.d).join('');
        layers.analysis.append(el('path', {class: 'k-an k-an-outhalo', d}), el('path', {class: 'k-an k-an-outage', d}));
        for (const line of outage) {
          const [x, y] = midpoint(line);
          const label = el('text', {x: r1(x), y: r1(y), dy: '-0.7em', class: 'k-an-text k-an-outtext', 'text-anchor': 'middle'});
          label.textContent = '사고';
          layers.analysis.append(label);
        }
      }
      const ranks = new Map();
      for (const item of overlaySpec.candidates || []) {
        const bus = model.busById.get(String(item.bus));
        if (bus) ranks.set(bus, [...(ranks.get(bus) || []), item.rank]);
      }
      out.candidates = [...ranks.keys()].map((bus) => bus.id);
      if (ranks.size) {
        layers.analysis.append(el('path', {class: 'k-an k-an-cand', d: [...ranks.keys()].map((bus) => dot(bus.xy)).join('')}));
        for (const [bus, list] of ranks) {
          const label = el('text', {x: r1(bus.xy[0]), y: r1(bus.xy[1]), dx: '0.75em', dy: '-0.55em', class: 'k-an-text k-an-rank'});
          label.textContent = list.join('·');
          layers.analysis.append(label);
        }
      }
      drawn = out;
      updateLabels();
    }

    /** spec: {outage, violationLines, violationTransformers, violationBuses, isolatedBuses, isolatedLines, monitored: [ids], candidates: [{bus, rank}]} or null. */
    function overlay(spec) {
      overlaySpec = spec || null;
      drawOverlay();
      return drawn ? JSON.parse(JSON.stringify(drawn)) : null;
    }

    function drawSelection() {
      layers.sel.replaceChildren();
      if (!selected) return;
      const {kind, item} = selected;
      if (kind === 'line') layers.sel.append(el('path', {class: 'k-sel-line', d: item.d}));
      else if (kind === 'transformer') layers.sel.append(el('path', {class: 'k-sel-dot', d: dot(item.mid)}));
      else layers.sel.append(el('path', {class: 'k-sel-dot', d: dot(item.xy)}));
    }

    function find(kind, id) {
      if (!model) return null;
      const table = {bus: model.busById, line: model.lineById, plant: model.plantById, transformer: model.trafoById}[kind];
      const item = table && table.get(String(id));
      return item ? {kind, item} : null;
    }

    function focus(hit) {
      if (!hit || !vb) return;
      const pts = hit.kind === 'line' ? hit.item.parts.flat() : [hit.kind === 'transformer' ? hit.item.mid : hit.item.xy];
      const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
      const cx = (Math.min(...xs) + Math.max(...xs)) / 2, cy = (Math.min(...ys) + Math.max(...ys)) / 2;
      const w = Math.min(vb.w, Math.max(base.w / 12, (Math.max(...xs) - Math.min(...xs)) * 2.4));
      const h = w * (vb.h / vb.w);
      vb = {x: cx - w / 2, y: cy - h / 2, w, h};
      setViewBox();
      updateLabels();
    }

    function select(hit, zoomTo) {
      selected = hit;
      drawSelection();
      if (hit && zoomTo) focus(hit);
      if (onSelect) onSelect(hit);
    }

    /** Client coordinates of a feature (for keyboard-free tests and the search list). */
    function screenPoint(kind, id) {
      const hit = find(kind, id);
      const m = svg.getScreenCTM();
      if (!hit || !m) return null;
      const p = kind === 'line' ? midpoint(hit.item) : kind === 'transformer' ? hit.item.mid : hit.item.xy;
      const q = new DOMPoint(p[0], p[1]).matrixTransform(m);
      return {x: q.x, y: q.y};
    }

    function search(query, limit = 8) {
      if (!model || !query) return [];
      const q = query.trim().toLowerCase();
      if (!q) return [];
      const out = [];
      const test = (kind, item, ...fields) => {
        if (out.length < limit && fields.some((f) => f && String(f).toLowerCase().includes(q))) out.push({kind, item});
      };
      for (const bus of model.buses) test('bus', bus, bus.id, bus.name, bus.nameEn);
      for (const line of model.lines) test('line', line, line.id);
      for (const plant of model.plants) test('plant', plant, plant.id, plant.name);
      for (const trafo of model.transformers) test('transformer', trafo, trafo.id);
      return out;
    }

    return {
      svg,
      show,
      clear,
      paint,
      overlay,
      overlayDrawn: () => (drawn ? JSON.parse(JSON.stringify(drawn)) : null),
      select: (kind, id, zoomTo = true) => select(find(kind, id), zoomTo),
      clearSelection: () => select(null, false),
      zoomBy: (factor) => zoomAt(factor),
      fit: () => { if (base) { vb = {...base}; setViewBox(); updateLabels(); } },
      search,
      screenPoint,
      viewBox: () => (vb ? {...vb} : null),
      counts: () => (model ? {buses: model.buses.length, lines: model.lines.length, transformers: model.transformers.length,
        plants: model.plants.length, nodes: svg.querySelectorAll('*').length} : null),
    };
  }

  return {create};
})();
