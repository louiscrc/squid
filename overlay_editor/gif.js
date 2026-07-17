import { GiphyFetch } from 'https://cdn.jsdelivr.net/npm/@giphy/js-fetch-api@5.6.0/+esm';

const GIPHY_API_KEY = '0YDmt4NVuQwNvF7UqKlR3k2pfBs11D5w';
const gf = new GiphyFetch(GIPHY_API_KEY);

const dial = document.getElementById('dial');
const previewGif = document.getElementById('previewGif');
const status = document.getElementById('status');
const fileInput = document.getElementById('file');
const queryEl = document.getElementById('query');
const resultsEl = document.getElementById('results');
const zoomEl = document.getElementById('zoom');
const panXEl = document.getElementById('panX');
const panYEl = document.getElementById('panY');
const bgColorEl = document.getElementById('bgColor');
const zoomVal = document.getElementById('zoomVal');
const panXVal = document.getElementById('panXVal');
const panYVal = document.getElementById('panYVal');
const btnApply = document.getElementById('btnApply');
const btnReset = document.getElementById('btnReset');
const btnSearch = document.getElementById('btnSearch');

let gifBytes = null;
let previewObjectUrl = null;
let drag = null;
let selectedUrl = null;

function setStatus(msg) {
  status.textContent = msg;
}

function framing() {
  return {
    zoom: Number(zoomEl.value) || 1,
    panX: Number(panXEl.value) || 0,
    panY: Number(panYEl.value) || 0,
    bgColor: bgColorEl.value || '#000000',
  };
}

function syncLabels() {
  const f = framing();
  zoomVal.textContent = f.zoom.toFixed(2);
  panXVal.textContent = f.panX.toFixed(2);
  panYVal.textContent = f.panY.toFixed(2);
}

function applyFraming() {
  const size = dial.clientWidth || 640;
  const { zoom, panX, panY, bgColor } = framing();
  const side = Math.max(1, size * zoom);
  const maxPan = Math.abs(size - side) / 2;
  const ox = maxPan * panX;
  const oy = maxPan * panY;
  dial.style.background = bgColor;
  previewGif.style.width = `${side}px`;
  previewGif.style.height = `${side}px`;
  previewGif.style.objectFit = 'cover';
  previewGif.style.transform = `translate(calc(-50% + ${ox}px), calc(-50% + ${oy}px))`;
}

function onFramingChange() {
  syncLabels();
  applyFraming();
}

function bytesToBase64(bytes) {
  let binary = '';
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}

function pickUrls(gif) {
  const images = gif.images || {};
  const preview =
    images.fixed_width?.url ||
    images.preview_gif?.url ||
    images.downsized?.url ||
    images.original?.url;
  const full =
    images.downsized?.url ||
    images.downsized_medium?.url ||
    images.original?.url ||
    preview;
  return { preview, full };
}

function showLivePreview(src) {
  previewGif.hidden = false;
  previewGif.src = src;
  previewGif.onload = () => applyFraming();
  applyFraming();
}

async function loadBytes(bytes, label) {
  gifBytes = bytes;
  if (previewObjectUrl) {
    URL.revokeObjectURL(previewObjectUrl);
    previewObjectUrl = null;
  }
  previewObjectUrl = URL.createObjectURL(new Blob([bytes], { type: 'image/gif' }));
  showLivePreview(previewObjectUrl);
  btnApply.disabled = false;
  setStatus(`${label} · ${(bytes.length / 1024).toFixed(0)} KB`);
}

async function loadFromProxyUrl(sourceUrl, label, livePreviewUrl) {
  // Animate immediately from CDN, then fetch bytes for Apply.
  showLivePreview(livePreviewUrl || sourceUrl);
  setStatus('Downloading…');
  btnApply.disabled = true;
  const res = await fetch('/gif/proxy?url=' + encodeURIComponent(sourceUrl));
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.error || res.statusText);
  }
  const buf = new Uint8Array(await res.arrayBuffer());
  await loadBytes(buf, label || 'GIF');
}

function renderResults(gifs) {
  resultsEl.innerHTML = '';
  if (!gifs.length) {
    resultsEl.innerHTML = '<p class="hint">No results</p>';
    return;
  }
  for (const gif of gifs) {
    const { preview, full } = pickUrls(gif);
    if (!full) {
      continue;
    }
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.title = gif.title || gif.slug || 'giphy';
    if (full === selectedUrl) {
      btn.classList.add('selected');
    }
    const img = document.createElement('img');
    img.loading = 'lazy';
    img.alt = gif.title || '';
    img.src = preview || full;
    btn.appendChild(img);
    btn.addEventListener('click', async () => {
      selectedUrl = full;
      for (const el of resultsEl.querySelectorAll('button')) {
        el.classList.toggle('selected', el === btn);
      }
      try {
        await loadFromProxyUrl(full, gif.title || 'Giphy', preview || full);
      } catch (e) {
        setStatus('Fetch failed: ' + e.message);
      }
    });
    resultsEl.appendChild(btn);
  }
}

async function runSearch() {
  const q = queryEl.value.trim();
  setStatus(q ? 'Searching Giphy…' : 'Loading trending…');
  btnSearch.disabled = true;
  try {
    const res = q
      ? await gf.search(q, { limit: 24, sort: 'relevant', rating: 'pg-13' })
      : await gf.trending({ limit: 24, rating: 'pg-13' });
    const gifs = res.data || [];
    renderResults(gifs);
    setStatus(q ? `${gifs.length} results for “${q}”` : `${gifs.length} trending`);
  } catch (e) {
    setStatus('Search failed: ' + (e.message || e));
  } finally {
    btnSearch.disabled = false;
  }
}

fileInput.addEventListener('change', async () => {
  const file = fileInput.files && fileInput.files[0];
  if (!file) {
    return;
  }
  selectedUrl = null;
  for (const el of resultsEl.querySelectorAll('button')) {
    el.classList.remove('selected');
  }
  try {
    const buf = new Uint8Array(await file.arrayBuffer());
    await loadBytes(buf, file.name);
  } catch (e) {
    setStatus('Read failed: ' + e);
    btnApply.disabled = true;
  }
});

btnSearch.addEventListener('click', runSearch);
queryEl.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') {
    runSearch();
  }
});

for (const el of [zoomEl, panXEl, panYEl, bgColorEl]) {
  el.addEventListener('input', onFramingChange);
}

btnReset.addEventListener('click', () => {
  zoomEl.value = '1';
  panXEl.value = '0';
  panYEl.value = '0';
  bgColorEl.value = '#000000';
  onFramingChange();
});

dial.addEventListener('pointerdown', (e) => {
  if (previewGif.hidden) return;
  dial.setPointerCapture(e.pointerId);
  drag = { x: e.clientX, y: e.clientY, panX: framing().panX, panY: framing().panY };
});
dial.addEventListener('pointermove', (e) => {
  if (!drag) return;
  const { zoom } = framing();
  const sens = zoom <= 1.01 ? 0.004 : 0.004 / Math.max(0.25, zoom - 1);
  panXEl.value = String(Math.max(-1, Math.min(1, drag.panX + (e.clientX - drag.x) * sens)));
  panYEl.value = String(Math.max(-1, Math.min(1, drag.panY + (e.clientY - drag.y) * sens)));
  onFramingChange();
});
dial.addEventListener('pointerup', () => {
  drag = null;
});
dial.addEventListener('pointercancel', () => {
  drag = null;
});

window.addEventListener('resize', applyFraming);

btnApply.addEventListener('click', async () => {
  if (!gifBytes) return;
  btnApply.disabled = true;
  const baseTitle = 'Kraken GIF';
  const { zoom, panX, panY, bgColor } = framing();

  const poll = setInterval(async () => {
    try {
      const p = await fetch('/gif/progress').then((r) => r.json());
      if (!p || !p.active) {
        return;
      }
      const pct = p.percent || 0;
      const phase = p.phase || 'Working';
      const detail =
        p.frames && p.frame
          ? ` · ${p.frame}/${p.frames}`
          : p.frames
            ? ` · ${p.frames} frames`
            : '';
      const label = `${phase}${detail} — ${pct}%`;
      setStatus(label);
      document.title = `${pct}% · ${baseTitle}`;
    } catch (_) {
      /* ignore poll errors while POST runs */
    }
  }, 200);

  try {
    setStatus('Starting… — 0%');
    document.title = `0% · ${baseTitle}`;
    const res = await fetch('/gif', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({
        raw: bytesToBase64(gifBytes),
        zoom,
        panX,
        panY,
        bgColor,
      }),
    });
    const data = await res.json();
    if (!res.ok || !data.ok) {
      throw new Error(data.error || res.statusText);
    }
    setStatus(`Applied · ${data.bytes} bytes on LCD`);
    document.title = baseTitle;
  } catch (e) {
    setStatus('Apply failed: ' + e.message);
    document.title = baseTitle;
  } finally {
    clearInterval(poll);
    btnApply.disabled = false;
  }
});

syncLabels();
applyFraming();
runSearch();
