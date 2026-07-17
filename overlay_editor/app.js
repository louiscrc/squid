(() => {
  const canvas = document.getElementById('canvas');
  const ctx = canvas.getContext('2d');
  const catalogEl = document.getElementById('catalog');
  const statusEl = document.getElementById('status');
  const propForm = document.getElementById('propForm');
  const noSelection = document.getElementById('noSelection');

  let layout = {
    version: 7,
    arcs: {
      enabled: true,
      colors: ['#3278ff', '#ffffff', '#ff3232'],
      left: { metric: 'cpu_temp_c', min: 20, max: 100 },
      right: { metric: 'gpu_temp_c', min: 20, max: 90 },
    },
    backgroundColor: '#000000',
    textColor: '#ffffff',
    labelColor: '#9a9a9a',
    widgets: [],
  };
  let catalog = [];
  let arcMetricKeys = [];
  let formatted = {};
  let partsMap = {};
  let values = {};
  let selectedId = null;
  let selectedArcSide = null; // 'left' | 'right' | null
  let drag = null;
  let dirty = false;

  const SIZE = 640;
  // Must match overlay_layout.LABEL_VALUE_GAP_PX / GLYPH_HALF_FACTOR
  const LABEL_VALUE_GAP_PX = 12;
  const GLYPH_HALF_FACTOR = 0.36;
  const FONT = 'RubikOverlay, Segoe UI, sans-serif';
  const ARC_SIDE_DEFAULTS = {
    left: { metric: 'cpu_temp_c', min: 20, max: 100, x: 70, y: 320, fontSize: 30, labelFontSize: 16, label: '' },
    right: { metric: 'gpu_temp_c', min: 20, max: 90, x: 570, y: 320, fontSize: 30, labelFontSize: 16, label: '' },
  };

  function widgetStackYs(y, fontSize, labelSize, hasLabel) {
    const valueCy = y;
    if (!hasLabel) return { labelCy: null, valueCy };
    const labelCy =
      valueCy -
      fontSize * GLYPH_HALF_FACTOR -
      LABEL_VALUE_GAP_PX -
      labelSize * GLYPH_HALF_FACTOR;
    return { labelCy, valueCy };
  }

  function metricFont(size, weight) {
    return `${weight || 700} ${size}px ${FONT}`;
  }

  function api(path, opts) {
    return fetch(path, opts).then((r) => {
      if (!r.ok) throw new Error(r.statusText);
      return r.json();
    });
  }

  function ensureArcSide(side, key) {
    const d = ARC_SIDE_DEFAULTS[key];
    if (!side || typeof side !== 'object') return { ...d };
    return {
      metric: side.metric || d.metric,
      min: side.min ?? d.min,
      max: side.max ?? d.max,
      x: side.x ?? d.x,
      y: side.y ?? d.y,
      fontSize: side.fontSize ?? d.fontSize,
      labelFontSize: side.labelFontSize ?? d.labelFontSize,
      label: side.label != null ? side.label : '',
    };
  }

  function ensureArcs() {
    if (!layout.arcs) {
      layout.arcs = {
        enabled: layout.tempArcs !== false,
        colors: ['#3278ff', '#ffffff', '#ff3232'],
        left: { ...ARC_SIDE_DEFAULTS.left },
        right: { ...ARC_SIDE_DEFAULTS.right },
      };
    }
    layout.arcs.left = ensureArcSide(layout.arcs.left, 'left');
    layout.arcs.right = ensureArcSide(layout.arcs.right, 'right');
    return layout.arcs;
  }

  function usedMetrics(exceptWidgetId) {
    const used = new Set();
    const arcs = ensureArcs();
    if (arcs.enabled) {
      if (arcs.left?.metric) used.add(arcs.left.metric);
      if (arcs.right?.metric) used.add(arcs.right.metric);
    }
    for (const w of layout.widgets) {
      if (exceptWidgetId && w.id === exceptWidgetId) continue;
      if (w.metric) used.add(w.metric);
    }
    return used;
  }

  function markDirty(v) {
    dirty = !!v;
    document.getElementById('btnSave').textContent = dirty ? 'Save *' : 'Save';
  }

  function selected() {
    return layout.widgets.find((w) => w.id === selectedId) || null;
  }

  function selectedArc() {
    if (!selectedArcSide) return null;
    const arcs = ensureArcs();
    return arcs[selectedArcSide] || null;
  }

  function canvasPos(ev) {
    const rect = canvas.getBoundingClientRect();
    const scaleX = SIZE / rect.width;
    const scaleY = SIZE / rect.height;
    return {
      x: (ev.clientX - rect.left) * scaleX,
      y: (ev.clientY - rect.top) * scaleY,
    };
  }

  function hitTestArc(x, y) {
    const arcs = ensureArcs();
    if (!arcs.enabled) return null;
    for (const key of ['left', 'right']) {
      const side = arcs[key];
      const dx = x - side.x;
      const dy = y - side.y;
      if (dx * dx + dy * dy < 48 * 48) return key;
    }
    return null;
  }

  function hitTest(x, y) {
    for (let i = layout.widgets.length - 1; i >= 0; i--) {
      const w = layout.widgets[i];
      const dx = x - w.x;
      const dy = y - w.y;
      if (dx * dx + dy * dy < 56 * 56) return w;
    }
    return null;
  }

  function gradientColor(u, colors) {
    u = Math.max(0, Math.min(1, u));
    const parse = (hex) => {
      const h = (hex || '#ffffff').replace('#', '');
      return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
    };
    const c0 = parse(colors[0]);
    const c1 = parse(colors[1]);
    const c2 = parse(colors[2]);
    let a, b, v;
    if (u < 0.5) {
      v = u * 2;
      a = c0;
      b = c1;
    } else {
      v = (u - 0.5) * 2;
      a = c1;
      b = c2;
    }
    return [
      Math.round(a[0] + (b[0] - a[0]) * v),
      Math.round(a[1] + (b[1] - a[1]) * v),
      Math.round(a[2] + (b[2] - a[2]) * v),
    ];
  }

  function arcUnit(side) {
    const metric = side.metric;
    const amin = Number(side.min);
    const amax = Number(side.max);
    if (metric === 'ram' || metric === 'vram') {
      const used = values[metric === 'ram' ? 'ram_used' : 'vram_used'];
      const total = values[metric === 'ram' ? 'ram_total' : 'vram_total'];
      if (used == null || !total) return null;
      return Math.max(0, Math.min(1, used / total));
    }
    let lo = amin;
    let hi = amax;
    if (metric.endsWith('_w')) {
      const mx = values[metric.replace('_w', '_max_w')];
      if (mx > 0) {
        lo = 0;
        hi = mx;
      }
    }
    const v = values[metric];
    if (v == null || hi <= lo) return null;
    return Math.max(0, Math.min(1, (v - lo) / (hi - lo)));
  }

  function drawHalf(cx, cy, r, width, startRad, endRad, u, colors, track, fromEnd) {
    ctx.lineWidth = width;
    ctx.lineCap = 'butt';
    ctx.strokeStyle = track;
    ctx.beginPath();
    ctx.arc(cx, cy, r, startRad, endRad);
    ctx.stroke();
    if (u == null || u <= 0) return;
    const span = endRad - startRad;
    const steps = Math.max(8, Math.round(56 * u));
    for (let i = 0; i < steps; i++) {
      let a0;
      let a1;
      if (fromEnd) {
        a0 = endRad - span * u * (i + 1) / steps;
        a1 = endRad - span * u * i / steps;
      } else {
        a0 = startRad + span * u * i / steps;
        a1 = startRad + span * u * (i + 1) / steps;
      }
      const [cr, cg, cb] = gradientColor(((i + 1) / steps) * u, colors);
      ctx.strokeStyle = `rgb(${cr},${cg},${cb})`;
      ctx.beginPath();
      ctx.arc(cx, cy, r, a0, a1);
      ctx.stroke();
    }
  }

  function metricTitle(key) {
    const m = catalog.find((c) => c.key === key);
    return m ? m.label : key;
  }

  function drawTempArcs() {
    const arcs = ensureArcs();
    if (!arcs.enabled) return;
    const width = 28;
    const pad = Math.floor(width / 2) + 1;
    const cx = SIZE / 2;
    const cy = SIZE / 2;
    const r = SIZE / 2 - pad;
    const bg = layout.backgroundColor || '#000000';
    const bgT = bg === 'transparent' || bg === 'none';
    const colors = arcs.colors || ['#3278ff', '#ffffff', '#ff3232'];
    const fgT = layout.textColor === 'transparent' || layout.textColor === 'none';
    const fg = fgT ? '#ffffff' : (layout.textColor || '#ffffff');
    const label = layout.labelColor || '#9a9a9a';

    drawHalf(cx, cy, r, width, Math.PI / 2, (3 * Math.PI) / 2, arcUnit(arcs.left), colors, bgT ? 'rgba(0,0,0,0)' : bg, false);
    drawHalf(cx, cy, r, width, (3 * Math.PI) / 2, Math.PI / 2 + Math.PI * 2, arcUnit(arcs.right), colors, bgT ? 'rgba(0,0,0,0)' : bg, true);

    // Always draw side readouts (even when text is transparent — punch preview skipped)
    for (const key of ['left', 'right']) {
      const side = arcs[key];
      const metric = side.metric;
      const title = (side.label && String(side.label).trim()) || metricTitle(metric);
      const fontSize = side.fontSize || 30;
      const labelSize = side.labelFontSize || 16;
      const { labelCy, valueCy } = widgetStackYs(side.y, fontSize, labelSize, !!title);
      const parts = partsMap[metric] || [{ text: formatted[metric] || '--', role: 'num' }];
      if (title && labelCy != null) {
        ctx.fillStyle = label;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.font = metricFont(labelSize);
        ctx.fillText(title, side.x, labelCy);
      }
      drawParts(parts, side.x, valueCy, fontSize, fg, label);
      if (selectedArcSide === key) {
        ctx.strokeStyle = '#3d8bfd';
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(side.x, side.y, 40, 0, Math.PI * 2);
        ctx.stroke();
      }
    }
  }

  function drawParts(parts, x, y, fontSize, fill, labelFill) {
    const unitSize = Math.max(10, Math.round(fontSize * 0.55));
    const totalSize = Math.max(9, Math.round(fontSize * 0.42));
    const tinySize = Math.max(9, Math.round(fontSize * 0.36));
    const grey = labelFill || fill;
    ctx.textAlign = 'left';
    ctx.textBaseline = 'middle';
    const measured = parts.map((p) => {
      let size = fontSize;
      if (p.role === 'unit') size = unitSize;
      else if (p.role === 'total') size = totalSize;
      else if (p.role === 'tiny') size = tinySize;
      ctx.font = metricFont(size);
      return { ...p, size, w: ctx.measureText(p.text).width };
    });
    let cursor = x - measured.reduce((s, p) => s + p.w, 0) / 2;
    for (const p of measured) {
      ctx.font = metricFont(p.size);
      ctx.fillStyle = p.role === 'num' ? fill : grey;
      ctx.fillText(p.text, cursor, y);
      cursor += p.w;
    }
  }

  function syncThemeInputs() {
    const bg = layout.backgroundColor || '#000000';
    const fg = layout.textColor || '#ffffff';
    const bgT = bg === 'transparent' || bg === 'none';
    const fgT = fg === 'transparent' || fg === 'none';
    document.getElementById('bgTransparent').checked = bgT;
    document.getElementById('textTransparent').checked = fgT;
    document.getElementById('bgColor').value = bgT ? '#000000' : bg;
    document.getElementById('textColor').value = fgT ? '#ffffff' : fg;
    document.getElementById('bgColor').disabled = bgT;
    document.getElementById('textColor').disabled = fgT;
    const arcs = ensureArcs();
    document.getElementById('arcsEnabled').checked = !!arcs.enabled;
    document.getElementById('arcC0').value = arcs.colors?.[0] || '#3278ff';
    document.getElementById('arcC1').value = arcs.colors?.[1] || '#ffffff';
    document.getElementById('arcC2').value = arcs.colors?.[2] || '#ff3232';
    document.getElementById('arcLeftMin').value = arcs.left?.min ?? 20;
    document.getElementById('arcLeftMax').value = arcs.left?.max ?? 100;
    document.getElementById('arcRightMin').value = arcs.right?.min ?? 20;
    document.getElementById('arcRightMax').value = arcs.right?.max ?? 90;
    fillArcSelects();
    document.getElementById('arcLeftMetric').value = arcs.left?.metric || 'cpu_temp_c';
    document.getElementById('arcRightMetric').value = arcs.right?.metric || 'gpu_temp_c';
    document.getElementById('arcsForm').style.opacity = arcs.enabled ? '1' : '0.45';
  }

  function draw() {
    const bgRaw = layout.backgroundColor || '#000000';
    const bgT = bgRaw === 'transparent' || bgRaw === 'none';
    const bg = bgT ? '#1a1a1a' : bgRaw; // editor preview stand-in for canvas
    const fgT = layout.textColor === 'transparent' || layout.textColor === 'none';
    const fg = fgT ? '#ffffff' : (layout.textColor || '#ffffff');
    const labelFg = layout.labelColor || '#9a9a9a';
    ctx.clearRect(0, 0, SIZE, SIZE);
    ctx.save();
    ctx.beginPath();
    ctx.arc(SIZE / 2, SIZE / 2, SIZE / 2 - 2, 0, Math.PI * 2);
    ctx.clip();
    // Fake canvas when BG is transparent
    if (bgT) {
      const g = ctx.createLinearGradient(0, 0, SIZE, SIZE);
      g.addColorStop(0, '#1a2030');
      g.addColorStop(1, '#2a1820');
      ctx.fillStyle = g;
    } else {
      ctx.fillStyle = bg;
    }
    ctx.fillRect(0, 0, SIZE, SIZE);

    ctx.strokeStyle = 'rgba(255,255,255,0.06)';
    ctx.beginPath();
    ctx.moveTo(SIZE / 2, 0);
    ctx.lineTo(SIZE / 2, SIZE);
    ctx.moveTo(0, SIZE / 2);
    ctx.lineTo(SIZE, SIZE / 2);
    ctx.stroke();

    drawTempArcs();

    for (const w of layout.widgets) {
      const parts = partsMap[w.metric] || [{ text: formatted[w.metric] || '--', role: 'num' }];
      const isSel = w.id === selectedId;
      const fontSize = w.fontSize || 28;
      const labelSize = w.labelFontSize || 12;
      const { labelCy, valueCy } = widgetStackYs(w.y, fontSize, labelSize, !!w.label);
      if (w.label && labelCy != null) {
        ctx.fillStyle = labelFg;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.font = metricFont(labelSize);
        ctx.fillText(w.label, w.x, labelCy);
      }
      drawParts(parts, w.x, valueCy, fontSize, fg, labelFg);
      if (isSel) {
        ctx.strokeStyle = '#3d8bfd';
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(w.x, w.y, 44, 0, Math.PI * 2);
        ctx.stroke();
      }
    }
    ctx.restore();

    ctx.strokeStyle = 'rgba(255,255,255,0.15)';
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.arc(SIZE / 2, SIZE / 2, SIZE / 2 - 2, 0, Math.PI * 2);
    ctx.stroke();
  }

  function renderCatalog() {
    const used = usedMetrics();
    catalogEl.innerHTML = '';
    for (const m of catalog) {
      const li = document.createElement('li');
      const taken = used.has(m.key);
      if (taken) li.classList.add('disabled');
      li.innerHTML = `<strong>${m.label}</strong><span class="val">${formatted[m.key] || '--'} · ${m.key}${taken ? ' (in use)' : ''}</span>`;
      if (!taken) li.addEventListener('click', () => addWidget(m));
      catalogEl.appendChild(li);
    }
  }

  function fillArcSelects() {
    const keys = arcMetricKeys.length ? arcMetricKeys : catalog.map((c) => c.key);
    for (const id of ['arcLeftMetric', 'arcRightMetric']) {
      const sel = document.getElementById(id);
      const cur = sel.value;
      sel.innerHTML = '';
      for (const key of keys) {
        const meta = catalog.find((c) => c.key === key);
        const opt = document.createElement('option');
        opt.value = key;
        opt.textContent = meta ? `${meta.label} (${key})` : key;
        sel.appendChild(opt);
      }
      if (cur) sel.value = cur;
    }
  }

  function fillMetricSelect() {
    const sel = document.getElementById('propMetric');
    const w = selected();
    const used = usedMetrics(w?.id);
    sel.innerHTML = '';
    for (const m of catalog) {
      if (used.has(m.key) && m.key !== w?.metric) continue;
      const opt = document.createElement('option');
      opt.value = m.key;
      opt.textContent = `${m.label} (${m.key})`;
      sel.appendChild(opt);
    }
  }

  function showProps() {
    const w = selected();
    const arc = selectedArc();
    const titleEl = document.getElementById('propsTitle');
    const metricWrap = document.getElementById('propMetricWrap');
    const xyWrap = document.getElementById('propXY');
    const delBtn = document.getElementById('btnDelete');

    if (!w && !arc) {
      propForm.classList.add('hidden');
      noSelection.classList.remove('hidden');
      return;
    }
    noSelection.classList.add('hidden');
    propForm.classList.remove('hidden');

    if (arc) {
      titleEl.textContent = selectedArcSide === 'left' ? 'Left arc readout' : 'Right arc readout';
      metricWrap.classList.add('hidden');
      xyWrap.classList.remove('hidden');
      delBtn.classList.add('hidden');
      document.getElementById('propLabel').value = arc.label || '';
      document.getElementById('propFont').value = arc.fontSize || 30;
      document.getElementById('propLabelFont').value = arc.labelFontSize || 16;
      document.getElementById('propX').value = arc.x;
      document.getElementById('propY').value = arc.y;
      return;
    }

    titleEl.textContent = 'Widget';
    metricWrap.classList.remove('hidden');
    xyWrap.classList.add('hidden');
    delBtn.classList.remove('hidden');
    fillMetricSelect();
    document.getElementById('propLabel').value = w.label || '';
    document.getElementById('propMetric').value = w.metric;
    document.getElementById('propFont').value = w.fontSize || 28;
    document.getElementById('propLabelFont').value = w.labelFontSize || 12;
  }

  function dropWidgetsUsing(metric) {
    layout.widgets = layout.widgets.filter((w) => w.metric !== metric);
    if (selected() && selected().metric === metric) selectedId = null;
  }

  function addWidget(meta) {
    if (usedMetrics().has(meta.key)) return;
    const id = 'w_' + Math.random().toString(36).slice(2, 9);
    layout.widgets.push({
      id,
      metric: meta.key,
      label: meta.label,
      x: 320,
      y: 320,
      fontSize: 56,
      labelFontSize: 18,
      color: layout.textColor || '#ffffff',
      align: 'center',
      format: meta.format || 'auto',
    });
    selectedId = id;
    selectedArcSide = null;
    markDirty(true);
    showProps();
    renderCatalog();
    draw();
  }

  function bindProps() {
    const bind = (id, fn) => {
      document.getElementById(id).addEventListener('input', () => {
        const w = selected();
        const arc = selectedArc();
        if (w) fn(w, null);
        else if (arc) fn(null, arc);
        else return;
        markDirty(true);
        draw();
      });
    };
    bind('propLabel', (w, arc) => {
      const v = document.getElementById('propLabel').value;
      if (w) w.label = v;
      if (arc) arc.label = v;
    });
    bind('propMetric', (w) => {
      if (!w) return;
      const next = document.getElementById('propMetric').value;
      if (usedMetrics(w.id).has(next)) return;
      w.metric = next;
      const meta = catalog.find((c) => c.key === next);
      if (meta) w.label = meta.label;
      renderCatalog();
    });
    bind('propFont', (w, arc) => {
      const v = Number(document.getElementById('propFont').value) || 28;
      if (w) w.fontSize = v;
      if (arc) arc.fontSize = v;
    });
    bind('propLabelFont', (w, arc) => {
      const v = Number(document.getElementById('propLabelFont').value) || 12;
      if (w) w.labelFontSize = v;
      if (arc) arc.labelFontSize = v;
    });
    bind('propX', (_w, arc) => {
      if (!arc) return;
      arc.x = Math.max(20, Math.min(620, Number(document.getElementById('propX').value) || arc.x));
    });
    bind('propY', (_w, arc) => {
      if (!arc) return;
      arc.y = Math.max(20, Math.min(620, Number(document.getElementById('propY').value) || arc.y));
    });
    document.getElementById('btnDelete').addEventListener('click', () => {
      if (!selectedId) return;
      layout.widgets = layout.widgets.filter((w) => w.id !== selectedId);
      selectedId = null;
      markDirty(true);
      showProps();
      renderCatalog();
      draw();
    });
    document.getElementById('bgColor').addEventListener('input', () => {
      layout.backgroundColor = document.getElementById('bgColor').value;
      markDirty(true);
      draw();
    });
    document.getElementById('textColor').addEventListener('input', () => {
      layout.textColor = document.getElementById('textColor').value;
      markDirty(true);
      draw();
    });
    document.getElementById('bgTransparent').addEventListener('change', () => {
      const on = document.getElementById('bgTransparent').checked;
      layout.backgroundColor = on ? 'transparent' : document.getElementById('bgColor').value;
      document.getElementById('bgColor').disabled = on;
      markDirty(true);
      draw();
    });
    document.getElementById('textTransparent').addEventListener('change', () => {
      const on = document.getElementById('textTransparent').checked;
      layout.textColor = on ? 'transparent' : document.getElementById('textColor').value;
      document.getElementById('textColor').disabled = on;
      markDirty(true);
      draw();
    });

    document.getElementById('arcsEnabled').addEventListener('change', () => {
      const arcs = ensureArcs();
      arcs.enabled = document.getElementById('arcsEnabled').checked;
      if (arcs.enabled) {
        dropWidgetsUsing(arcs.left.metric);
        dropWidgetsUsing(arcs.right.metric);
      } else {
        selectedArcSide = null;
      }
      markDirty(true);
      syncThemeInputs();
      renderCatalog();
      showProps();
      draw();
    });
    const bindArc = (id, fn) => {
      document.getElementById(id).addEventListener('input', () => {
        fn(ensureArcs());
        markDirty(true);
        renderCatalog();
        draw();
      });
    };
    bindArc('arcC0', (a) => { a.colors[0] = document.getElementById('arcC0').value; });
    bindArc('arcC1', (a) => { a.colors[1] = document.getElementById('arcC1').value; });
    bindArc('arcC2', (a) => { a.colors[2] = document.getElementById('arcC2').value; });
    bindArc('arcLeftMin', (a) => { a.left.min = Number(document.getElementById('arcLeftMin').value); });
    bindArc('arcLeftMax', (a) => { a.left.max = Number(document.getElementById('arcLeftMax').value); });
    bindArc('arcRightMin', (a) => { a.right.min = Number(document.getElementById('arcRightMin').value); });
    bindArc('arcRightMax', (a) => { a.right.max = Number(document.getElementById('arcRightMax').value); });
    bindArc('arcLeftMetric', (a) => {
      const m = document.getElementById('arcLeftMetric').value;
      if (a.right.metric === m) return;
      a.left.metric = m;
      dropWidgetsUsing(m);
      showProps();
    });
    bindArc('arcRightMetric', (a) => {
      const m = document.getElementById('arcRightMetric').value;
      if (a.left.metric === m) return;
      a.right.metric = m;
      dropWidgetsUsing(m);
      showProps();
    });
  }

  canvas.addEventListener('mousedown', (ev) => {
    const p = canvasPos(ev);
    const w = hitTest(p.x, p.y);
    const arcKey = w ? null : hitTestArc(p.x, p.y);
    selectedId = w ? w.id : null;
    selectedArcSide = arcKey;
    showProps();
    draw();
    if (w) {
      drag = { type: 'widget', id: w.id, ox: p.x - w.x, oy: p.y - w.y };
    } else if (arcKey) {
      const side = ensureArcs()[arcKey];
      drag = { type: 'arc', side: arcKey, ox: p.x - side.x, oy: p.y - side.y };
    }
  });
  window.addEventListener('mousemove', (ev) => {
    if (!drag) return;
    const p = canvasPos(ev);
    if (drag.type === 'widget') {
      const w = layout.widgets.find((x) => x.id === drag.id);
      if (!w) return;
      w.x = Math.max(40, Math.min(SIZE - 40, Math.round(p.x - drag.ox)));
      w.y = Math.max(40, Math.min(SIZE - 40, Math.round(p.y - drag.oy)));
    } else if (drag.type === 'arc') {
      const side = ensureArcs()[drag.side];
      side.x = Math.max(40, Math.min(SIZE - 40, Math.round(p.x - drag.ox)));
      side.y = Math.max(40, Math.min(SIZE - 40, Math.round(p.y - drag.oy)));
      if (document.getElementById('propX')) {
        document.getElementById('propX').value = side.x;
        document.getElementById('propY').value = side.y;
      }
    }
    markDirty(true);
    draw();
  });
  window.addEventListener('mouseup', () => { drag = null; });

  document.getElementById('btnSave').addEventListener('click', async () => {
    try {
      layout = await api('/overlay/layout', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(layout),
      });
      markDirty(false);
      syncThemeInputs();
      renderCatalog();
      statusEl.textContent = 'Layout saved';
      draw();
    } catch (e) {
      statusEl.textContent = 'Save failed: ' + e.message;
    }
  });

  document.getElementById('btnReset').addEventListener('click', async () => {
    if (!confirm('Reset to default layout?')) return;
    try {
      layout = await api('/overlay/layout/reset', { method: 'POST', body: '{}' });
      selectedId = null;
      selectedArcSide = null;
      markDirty(false);
      syncThemeInputs();
      showProps();
      renderCatalog();
      draw();
      statusEl.textContent = 'Default layout restored';
    } catch (e) {
      statusEl.textContent = 'Reset failed: ' + e.message;
    }
  });

  async function refreshMetrics() {
    try {
      const m = await api('/metrics');
      catalog = m.catalog || [];
      arcMetricKeys = m.arcMetrics || [];
      formatted = m.formatted || {};
      partsMap = m.parts || {};
      values = m.values || {};
      const ab = m.afterburner ? 'Afterburner OK' : 'Afterburner offline';
      statusEl.textContent = ab + (dirty ? ' · unsaved changes' : '');
      fillArcSelects();
      renderCatalog();
      if (selected()) fillMetricSelect();
      draw();
    } catch (e) {
      statusEl.textContent = 'Bridge unreachable';
    }
  }

  async function boot() {
    bindProps();
    try {
      await document.fonts.load('700 64px RubikOverlay');
      await document.fonts.ready;
    } catch (_) { /* fallback to Segoe UI */ }
    layout = await api('/overlay/layout');
    syncThemeInputs();
    await refreshMetrics();
    showProps();
    draw();
    setInterval(refreshMetrics, 500);
  }

  boot().catch((e) => {
    statusEl.textContent = 'Init failed: ' + e.message;
  });
})();
