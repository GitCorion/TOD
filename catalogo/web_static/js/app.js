import { api, apiPost, onAuthRequired, onLiveChange } from './api.js';
import { attentionCard, attentionCount } from './attention.js';
import { esc, fmtBytes, fmtDate, fmtDuration, fmtEta, fmtInt, pct, debounce } from './format.js';
import { healthStatus, jobLabel, jobState, qbitState, taskDef } from './labels.js';
import { navigate, parseRoute, route, updateQuery } from './router.js';
import { addSearchResult, submitAdd, uploadTorrent } from './views/add.js';
import { state, toggleSelection } from './store.js';
import {
  $, $$, closeModal, confirmModal, icon, openModal, poster, restoreFocus, setLive,
  showResult, status, toast,
} from './ui.js';

function syncThemeButton() {
  const theme = document.documentElement.dataset.theme === 'light' ? 'light' : 'dark';
  const use = $('#themeIcon use');
  const button = $('#themeToggle');
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute('content', theme === 'dark' ? '#0f1114' : '#eef0f3');
  if (use) use.setAttribute('href', theme === 'dark' ? '#i-sun' : '#i-moon');
  if (button) {
    button.setAttribute(
      'aria-label',
      theme === 'dark' ? 'Cambiar a tema claro' : 'Cambiar a tema oscuro',
    );
    button.title = theme === 'dark' ? 'Cambiar a tema claro' : 'Cambiar a tema oscuro';
  }
}

function toggleTheme() {
  const next = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light';
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem('catalogo-theme', next); } catch (_) { /* sin almacenamiento */ }
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute('content', next === 'dark' ? '#0f1114' : '#eef0f3');
  syncThemeButton();
}

/* ------------------------------------------------------------------ sesión */

function showLogin() {
  $('#login').classList.remove('hidden');
}

function hideLogin() {
  $('#login').classList.add('hidden');
}

async function boot() {
  try {
    const session = await api('/api/session');
    state.session = session;
    $('#logoutBtn').classList.toggle('hidden', !session.auth_required);
    if (session.auth_required && !session.authenticated) {
      showLogin();
      return;
    }
    hideLogin();
    setLive(true);
    syncThemeButton();
    await route();
    refreshActivity();
    refreshAttention();
  } catch (error) {
    setLive(false);
    $('#view').innerHTML = `<div class="panel pad"><div class="empty">
      <strong>Sin conexión</strong>
      <p class="muted">${esc(error.message)}</p>
      <button class="btn" data-action="refresh">Reintentar</button>
    </div></div>`;
  }
}

/* ------------------------------------------------------------- selección */

function clearCatalogSelectionUI() {
  state.selection.clear();
  $$('[data-action="catalog-select"]').forEach((input) => { input.checked = false; });
  $$('.media-card.selected, .media-row.selected, .catalog-table tr.selected').forEach((node) => node.classList.remove('selected'));
  renderSelectionBar();
}

function clearTorrentSelectionUI() {
  state.torrentSelection.clear();
  $$('[data-action="torrent-select"]').forEach((input) => { input.checked = false; });
  renderSelectionBar();
}

function renderSelectionBar() {
  const bar = $('#selectionBar');
  if (!bar) return;
  if (state.view === 'catalog' && state.selection.size) {
    bar.classList.remove('hidden');
    bar.innerHTML = `<span><strong>${state.selection.size}</strong> ${state.selection.size === 1 ? 'seleccionado' : 'seleccionados'}</span>
      <button class="btn btn-sm btn-primary" data-action="bulk-publish">${icon('plus', 'sm')} Añadir seleccionados</button>
      <button class="btn btn-sm btn-ghost" data-action="bulk-download">${icon('download', 'sm')} Descargar ahora</button>
      <button class="btn btn-sm btn-icon" data-action="bulk-clear" aria-label="Quitar selección">${icon('close')}</button>`;
    return;
  }
  if (state.view === 'downloads' && state.torrentSelection.size) {
    bar.classList.remove('hidden');
    bar.innerHTML = `<span><strong>${state.torrentSelection.size}</strong> torrent${state.torrentSelection.size === 1 ? '' : 's'}</span>
      <button class="btn btn-sm btn-ghost" data-action="qbit-bulk" data-qaction="pause">${icon('pause', 'sm')} Pausar</button>
      <button class="btn btn-sm btn-ghost" data-action="qbit-bulk" data-qaction="resume">${icon('play', 'sm')} Reanudar</button>
      <button class="btn btn-sm btn-icon" data-action="torrent-clear" aria-label="Quitar selección">${icon('close')}</button>`;
    return;
  }
  bar.classList.add('hidden');
  bar.innerHTML = '';
}

/* --------------------------------------------------------------- actividad */

let activityTimer = null;

function setActivity(data) {
  state.activity = data;
  const active = (data?.active || []);
  const running = data?.running || active.find((job) => job.state === 'running') || active[0];
  const pill = $('#activityPill');
  const text = $('#activityText');
  const bar = $('#activityBar');
  const navJobs = $('#navJobs');
  const navMonitor = $('#navMonitor');
  if (navMonitor) navMonitor.textContent = data?.monitored ? String(data.monitored) : '';

  if (running) {
    const label = `${jobState(running.state).label}, ${jobLabel(running.kind)}`;
    if (pill) {
      pill.classList.remove('hidden');
      text.textContent = `${jobLabel(running.kind)} ${Math.round((running.progress || 0) * 100)} %`;
      pill.href = '#/jobs';
    }
    if (navJobs) navJobs.textContent = String(active.length);
    if (bar) {
      bar.innerHTML = `<span class="spinner" aria-hidden="true"></span>
        <strong>${esc(jobLabel(running.kind))}</strong>
        <span class="activity-message">${esc(running.message || label)}</span>
        <div class="bar"><i style="width:${pct(running.progress || 0)}%"></i></div>
        <span class="tiny faint">${Math.round((running.progress || 0) * 100)} %</span>
        <a class="btn btn-sm btn-ghost" href="#/jobs">Ver tarea</a>`;
      bar.classList.remove('hidden');
    }
  } else {
    if (pill) pill.classList.add('hidden');
    if (bar) bar.classList.add('hidden');
    if (navJobs) navJobs.textContent = '';
  }

  const previous = state.activeIds || [];
  const current = active.map((job) => job.id);
  const latest = data?.latest;
  for (const id of previous) {
    if (!current.includes(id) && latest && latest.id === id) {
      const kind = jobState(latest.state);
      const published = latest.kind === 'publish-now' && latest.result
        ? Number(latest.result.published || 0)
        : null;
      toast(
        `${jobLabel(latest.kind)}: ${kind.label.toLowerCase()}${published !== null ? `, ${fmtInt(published)} añadidos` : ''}`,
        latest.state === 'error' || (published === 0) ? 'warn' : 'ok',
        `<a class="btn btn-sm btn-ghost" href="#/jobs">Ver historial</a>`,
      );
      if (['jobs', 'dashboard', 'downloads', 'title', 'series', 'collection', 'catalog', 'add', 'monitor'].includes(state.view) && $('#modal').classList.contains('hidden')) {
        route();
      }
    }
  }
  state.activeIds = current;
}

async function refreshActivity() {
  if (document.hidden) return;
  try {
    const data = await api('/api/activity');
    setActivity(data);
  } catch (_) { /* la barra no debe romper la vista */ }
}

function startActivityPolling() {
  if (activityTimer) clearInterval(activityTimer);
  activityTimer = setInterval(refreshActivity, 5000);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) refreshActivity();
  });
}

/* -------------------------------------------------------------- atención */

let attentionTimer = null;

function setAttention(data) {
  state.attention = data;
  const count = attentionCount(data);
  const fails = Number(data?.counts?.fail || 0);
  const node = $('#navAttention');
  if (node) {
    node.textContent = count ? String(count) : '';
    node.classList.toggle('hidden', !count);
    node.classList.toggle('fail', fails > 0);
    node.classList.toggle('warn', fails === 0 && count > 0);
    node.setAttribute(
      'aria-label',
      count ? `${count} avisos pendientes` : 'Sin avisos pendientes',
    );
    node.title = count ? `${count} avisos pendientes` : 'Sin avisos pendientes';
  }
  const panel = $('#attentionPanel');
  const signature = JSON.stringify({
    counts: data?.counts,
    items: (data?.items || []).map((item) => [
      item.key, item.severity, item.detail, item.hint, item.since,
    ]),
  });
  if (panel && state.attentionSignature !== signature) {
    panel.outerHTML = attentionCard(data, { compact: state.view !== 'system' });
    state.attentionSignature = signature;
  }
}

async function refreshAttention() {
  if (document.hidden) return;
  try {
    setAttention(await api('/api/attention'));
  } catch (_) { /* el indicador no debe romper la vista */ }
}

function startAttentionPolling() {
  if (attentionTimer) clearInterval(attentionTimer);
  attentionTimer = setInterval(refreshAttention, 30000);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) refreshAttention();
  });
}

async function runAttentionCheck() {
  try {
    const result = await apiPost('/api/attention/check');
    toast(`Comprobación en cola (#${result.job_id})`, 'ok');
    let tries = 0;
    const timer = setInterval(async () => {
      tries += 1;
      try {
        const job = await api(`/api/jobs/${result.job_id}`);
        if (['ok', 'error', 'cancelled'].includes(job.state) || tries >= 15) {
          clearInterval(timer);
          await refreshAttention();
        }
      } catch (_) {
        if (tries >= 15) clearInterval(timer);
      }
    }, 2000);
  } catch (error) {
    toast(error.message, 'error');
  }
}

/* --------------------------------------------------------------- paleta */

let paletteTimer = null;

const DESTINATIONS = [
  { label: 'Inicio', href: '#/dashboard', hint: 'Contenido' },
  { label: 'Biblioteca', href: '#/catalog', hint: 'Contenido' },
  { label: 'Calendario', href: '#/calendar', hint: 'Contenido' },
  { label: 'Seguimiento', href: '#/monitor', hint: 'Contenido' },
  { label: 'Descargas', href: '#/downloads', hint: 'Actividad' },
  { label: 'Tareas', href: '#/jobs', hint: 'Actividad' },
  { label: 'Historial', href: '#/history', hint: 'Actividad' },
  { label: 'Siembra', href: '#/seeds', hint: 'Actividad' },
  { label: 'Estado', href: '#/system', hint: 'Sistema' },
  { label: 'Configuración', href: '#/settings', hint: 'Sistema' },
  { label: 'Añadir contenido', href: '#/add', hint: 'Acción' },
  { label: 'Detalle técnico', href: '#/technical', hint: 'Sistema' },
];

function plain(text) {
  return String(text).normalize('NFD').replace(/\p{Diacritic}/gu, '').toLowerCase();
}

function destinationItems(text) {
  const query = plain(text.trim());
  return DESTINATIONS
    .filter((item) => !query
      || plain(item.label).includes(query)
      || plain(item.hint).includes(query))
    .slice(0, 6)
    .map((item) => ({ ...item, nav: true }));
}

function paletteItems(items) {
  if (!items.length) {
    return `<div class="palette-empty">Sin resultados. Prueba en TMDB desde el enlace inferior.</div>`;
  }
  let mode = null;
  return items.map((item, index) => {
    let group = '';
    if (item.nav && mode !== 'nav') {
      mode = 'nav';
      group = '<div class="palette-group">Ir a</div>';
    }
    if (!item.nav && mode !== 'catalog') {
      mode = 'catalog';
      group = '<div class="palette-group">Biblioteca</div>';
    }
    if (item.nav) {
      return `${group}<a class="palette-item ${index === state.paletteIndex ? 'active' : ''}" href="${esc(item.href)}" data-palette-index="${index}">
        <span class="palette-icon" aria-hidden="true"><svg class="icon"><use href="#i-chevron"/></svg></span>
        <div class="info"><strong>${esc(item.label)}</strong><small>${esc(item.hint)}</small></div>
        <svg class="icon" aria-hidden="true"><use href="#i-arrow-right"/></svg>
      </a>`;
    }
    const target = item.kind === 'series'
      ? `#/series/${item.tmdb}`
      : `#/title/${item.id}`;
    const meta = item.kind === 'series'
      ? `Serie, ${item.year || '—'}`
      : `Película, ${item.year || '—'}`;
    const thumbUrl = /^https?:\/\//i.test(item.poster_path || '')
      ? item.poster_path
      : `https://image.tmdb.org/t/p/w92${item.poster_path || ''}`;
    const thumb = item.poster_path
      ? `<img class="thumb" src="${esc(thumbUrl)}" alt="" loading="lazy">`
      : `<span class="thumb placeholder">${esc(String(item.title || '?').slice(0, 1).toUpperCase())}</span>`;
    return `${group}<a class="palette-item ${index === state.paletteIndex ? 'active' : ''}" href="${target}" data-palette-index="${index}">
      ${thumb}
      <div class="info"><strong>${esc(item.title || '—')}</strong><small>${esc(meta)}</small></div>
      <svg class="icon" aria-hidden="true"><use href="#i-chevron"/></svg>
    </a>`;
  }).join('');
}

function renderPalette() {
  const box = $('#paletteResults');
  if (!box) return;
  box.innerHTML = paletteItems(state.paletteItems);
}

async function searchPalette(text) {
  const query = text.trim();
  const destinations = destinationItems(query);
  if (query.length < 2) {
    state.paletteItems = destinations;
    state.paletteIndex = 0;
    renderPalette();
    return;
  }
  try {
    const data = await api(`/api/search?q=${encodeURIComponent(query)}&limit=8`);
    state.paletteItems = [...destinations, ...(data.items || [])];
    state.paletteIndex = 0;
    renderPalette();
  } catch (error) {
    state.paletteItems = destinations;
    renderPalette();
    toast(error.message, 'error');
  }
}

function openShortcuts() {
  openModal(
    'Atajos de teclado',
    `<div class="kv"><span>Buscar en la biblioteca</span><kbd>Ctrl K</kbd></div>
     <div class="kv"><span>Cerrar diálogo o menú</span><kbd>Esc</kbd></div>
     <div class="kv"><span>Abrir esta ayuda</span><kbd>?</kbd></div>
     <div class="kv"><span>Abrir ficha o resultado activo</span><kbd>Enter</kbd></div>`,
    `<button class="btn btn-ghost" data-action="modal-close">Cerrar</button>`,
    { slim: true },
  );
}

let paletteFocus = null;
let paletteTrap = null;

function openPalette() {
  const palette = $('#palette');
  paletteFocus = document.activeElement;
  palette.classList.remove('hidden');
  document.body.style.overflow = 'hidden';
  const input = $('#paletteInput');
  input.value = '';
  state.paletteItems = destinationItems('');
  state.paletteIndex = 0;
  renderPalette();
  if (paletteTrap) document.removeEventListener('keydown', paletteTrap);
  paletteTrap = (event) => {
    if (event.key !== 'Tab') return;
    const focusable = $$('a, button, input, [tabindex]:not([tabindex="-1"])', palette)
      .filter((el) => !el.disabled && el.offsetParent !== null);
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };
  document.addEventListener('keydown', paletteTrap);
  input.focus();
}

function closePalette() {
  $('#palette').classList.add('hidden');
  document.body.style.overflow = '';
  if (paletteTrap) {
    document.removeEventListener('keydown', paletteTrap);
    paletteTrap = null;
  }
  if (paletteFocus && typeof paletteFocus.focus === 'function') {
    paletteFocus.focus({ preventScroll: true });
  }
  paletteFocus = null;
}

function openActivePaletteItem() {
  const item = state.paletteItems[state.paletteIndex];
  if (!item) {
    const text = $('#paletteInput').value.trim();
    if (text) {
      closePalette();
      navigate('add', [], { q: text });
    }
    return;
  }
  closePalette();
  if (item.nav) { location.hash = item.href; return; }
  if (item.kind === 'series') navigate('series', [item.tmdb]);
  else navigate('title', [item.id]);
}

/* ------------------------------------------------------------------ tareas */

function fieldHtml(field) {
  if (field.type === 'checkbox') {
    return `<label class="inline check" style="align-self:end;padding-bottom:6px">
      <input type="checkbox" name="${field.name}" ${field.value ? 'checked' : ''}> ${esc(field.label)}
    </label>`;
  }
  if (field.type === 'select') {
    return `<label class="field"><span>${esc(field.label)}</span><select name="${field.name}">
      ${field.options.map(([value, text]) => `<option value="${esc(value)}" ${String(field.value) === String(value) ? 'selected' : ''}>${esc(text)}</option>`).join('')}
    </select></label>`;
  }
  return `<label class="field"><span>${esc(field.label)}</span><input name="${field.name}" type="${field.type || 'text'}"
    ${field.value !== undefined && field.value !== null ? `value="${esc(field.value)}"` : ''}
    ${field.min !== undefined ? `min="${field.min}"` : ''}
    ${field.max !== undefined ? `max="${field.max}"` : ''}
    ${field.step !== undefined ? `step="${field.step}"` : ''}></label>`;
}

function openTask(kind) {
  const task = taskDef(kind);
  if (!task) { toast('Tarea desconocida', 'error'); return; }
  openModal(
    task.title,
    `<p class="muted" style="margin-top:0">${esc(task.desc)}</p>
     <form id="taskForm" data-kind="${kind}">
       <div class="form-grid">${task.fields.map(fieldHtml).join('')}</div>
     </form>
     ${task.warning ? `<p class="notice" style="margin-bottom:0">${esc(task.warning)}</p>` : ''}
     ${task.danger ? '<p class="notice" style="margin-bottom:0">Esta tarea puede modificar o borrar datos. Revisa primero el dry-run.</p>' : ''}`,
    `<button class="btn btn-ghost" data-action="modal-close">Cancelar</button>
     <button class="btn btn-primary" data-action="submit-task" data-kind="${kind}">Ejecutar</button>`,
    { wide: true },
  );
}

function formValues(form) {
  const values = {};
  for (const element of Array.from(form.elements)) {
    if (!element.name) continue;
    if (element.type === 'checkbox') values[element.name] = element.checked;
    else if (element.type === 'number') values[element.name] = element.value === '' ? null : Number(element.value);
    else values[element.name] = element.value;
  }
  for (const key of Object.keys(values)) {
    if (values[key] === '' || values[key] === null) delete values[key];
  }
  return values;
}

async function submitTask(kind) {
  const form = $('#taskForm');
  if (!form) return;
  const task = taskDef(kind);
  const params = formValues(form);
  if (params.years && typeof params.years === 'string') {
    params.years = params.years.split(',').map((value) => Number(value.trim())).filter((value) => value > 0);
  }
  if (task?.danger && params.execute) {
    const ok = await confirmModal({
      title: 'Ejecutar sin dry-run',
      message: `«${task.title}» va a modificar datos reales. ¿Continuar?`,
      confirm: 'Ejecutar',
      danger: true,
    });
    if (!ok) return;
  }
  try {
    const result = await apiPost('/api/jobs', { kind, params });
    closeModal();
    toast(`Tarea #${result.job_id} en cola`, 'ok',
      `<button class="btn btn-sm btn-ghost" data-action="job-detail" data-id="${result.job_id}">Ver tarea</button>`);
    refreshActivity();
    if (state.view === 'jobs') route();
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function showJob(id) {
  try {
    const job = await api(`/api/jobs/${id}`);
    const body = `
      <div class="kv"><span>Tipo</span><span>${esc(jobLabel(job.kind))} <span class="faint">(${esc(job.kind)})</span></span></div>
      <div class="kv"><span>Estado</span><span>${esc(jobState(job.state).label)}</span></div>
      <div class="kv"><span>Creada</span><span>${fmtDate(job.created_at)}</span></div>
      <div class="kv"><span>Duración</span><span>${fmtDuration(job.started_at, job.finished_at)}</span></div>
      <div class="kv"><span>Progreso</span><span>${Math.round((job.progress || 0) * 100)} %</span></div>
      <div class="kv"><span>Mensaje</span><span>${esc(job.message || '—')}</span></div>
      ${job.error ? `<h3 class="error">Error</h3><pre class="result">${esc(job.error)}</pre>` : ''}
      ${job.result ? `<details class="fold"><summary>Resultado</summary><pre class="result" style="border:none;border-radius:0;max-height:40vh">${esc(JSON.stringify(job.result, null, 2))}</pre></details>` : ''}
      ${job.params && Object.keys(job.params).length ? `<details class="fold" style="margin-top:8px"><summary>Parámetros</summary><pre class="result" style="border:none;border-radius:0">${esc(JSON.stringify(job.params, null, 2))}</pre></details>` : ''}
    `;
    openModal(`Tarea #${job.id}`,
      body,
      `${job.state === 'running' || job.state === 'queued' ? `<button class="btn btn-danger" data-action="job-cancel" data-id="${job.id}">Cancelar</button>` : ''}
       <button class="btn btn-ghost" data-action="modal-close">Cerrar</button>`,
      { wide: true });
  } catch (error) {
    toast(error.message, 'error');
  }
}

/* ------------------------------------------------------- buscar pendientes */

/* --------------------------------------------------- vista previa TMDB */

function openTmdbPreview(tmdb, kind, title) {
  const item = (state.tmdb.results || []).find(
    (entry) => Number(entry.tmdb) === Number(tmdb) && entry.kind === kind,
  );
  if (!item) {
    toast('No se pudo cargar la vista previa', 'warn');
    return;
  }
  const isSeries = item.kind === 'series';
  const external = `https://www.themoviedb.org/${isSeries ? 'tv' : 'movie'}/${item.tmdb}`;
  const action = item.already
    ? `<a class="btn btn-primary" href="${isSeries ? `#/series/${item.tmdb}` : `#/title/${item.asset_id}`}" data-action="modal-close">Abrir ficha</a>`
    : `<button class="btn btn-primary" data-action="${isSeries ? 'add-series' : 'add-movie'}" data-tmdb="${item.tmdb}" data-title="${esc(item.title)}">${icon('plus')} Añadir</button>`;
  openModal(
    item.title || 'Vista previa',
    `<div class="detail-hero">
      <div class="poster">${poster(item.poster_path, item.title)}</div>
      <div class="info">
        <h2>${esc(item.title || '—')} ${item.year ? `<span class="muted" style="font-size:15px;font-weight:400">(${item.year})</span>` : ''}</h2>
        <div class="row">${status(item.already ? 'Añadido' : 'Sin añadir', item.already ? 'info' : 'muted')}</div>
        <p class="overview">${esc(item.overview || 'Sin sinopsis en TMDB.')}</p>
      </div>
    </div>`,
    `<a class="btn btn-ghost" href="${external}" target="_blank" rel="noreferrer">${icon('external')} TMDB</a>
     <button class="btn btn-ghost" data-action="modal-close">Cerrar</button>
     ${action}`,
    { wide: true },
  );
}

/* ------------------------------------------------------- añadir título */

function openLibraryTitle(id, key, title, releases) {
  openModal(
    'Añadir',
    `<p class="muted" style="margin-top:0">Se añadirá el título.</p>
     <label class="inline check">
       <input type="checkbox" name="monitored" ${Number(releases) ? '' : 'checked'}>
       Seguir para buscar fuentes a diario
     </label>`,
    `<button class="btn btn-ghost" data-action="modal-close">Cancelar</button>
     <button class="btn btn-primary" data-action="library-title-start" data-id="${id}" data-key="${esc(key)}" data-releases="${releases}">Añadir</button>`,
    { slim: true },
  );
}

async function startLibraryTitle(element) {
  const monitored = Boolean($('#modalBody input[name="monitored"]')?.checked);
  const id = element.dataset.id;
  const key = element.dataset.key;
  const releases = Number(element.dataset.releases || 0);
  try {
    await apiPost(`/api/titles/${id}/library`, { member: true, monitored });
    if (monitored) {
      await apiPost(`/api/titles/${id}/monitor`, { monitored: true });
    }
    closeModal();
    if (releases && key) {
      await acquireKeys([key], key.startsWith('series:') ? 'series' : 'movie', 'publish');
    } else {
      toast(monitored ? 'Añadido y en seguimiento' : 'Añadido', 'ok');
    }
    await route();
  } catch (error) {
    toast(error.message, 'error');
  }
}

/* --------------------------------------------------------- añadir serie */

function openLibrarySeries(tmdb, title, season, mode = 'add') {
  const detail = state.seriesDetail;
  const seasons = detail?.seasons || [];
  const selected = season
    ? seasons.filter((entry) => entry.season === Number(season))
    : seasons;
  const episodes = selected.flatMap((entry) => entry.episodes || []);
  const missing = episodes.filter((episode) => !episode.in_catalog).length;
  const missingLibrary = episodes.filter((episode) => !episode.library_added).length;
  const needsSearch = episodes.some((episode) => !episode.published);
  const heading = mode === 'add'
    ? (season ? `Añadir temporada ${season}` : 'Añadir serie')
    : (season ? `Añadir episodios de la temporada ${season}` : 'Añadir episodios que faltan');
  const intro = mode === 'add'
    ? `Se añadirán los episodios que falten.${missing ? ` ${fmtInt(missing)} todavía no están en TOD.` : ''}`
    : `Se añadirán ${fmtInt(missingLibrary)} episodios que faltan.${missing ? ` ${fmtInt(missing)} todavía no están en TOD.` : ''}`;
  const action = mode === 'add' ? 'Añadir' : 'Añadir episodios';
  const monitoredByDefault = needsSearch ? 'checked' : '';
  openModal(
    heading,
    `<p class="muted" style="margin-top:0">${intro}</p>
     <label class="inline check">
       <input type="checkbox" name="monitored" ${monitoredByDefault}>
       Seguir para buscar fuentes a diario
     </label>`,
    `<button class="btn btn-ghost" data-action="modal-close">Cancelar</button>
     <button class="btn btn-primary" data-action="library-series-start" data-tmdb="${tmdb}" data-mode="${mode}" ${season ? `data-season="${season}"` : ''}>${action}</button>`,
    { slim: true },
  );
}

async function startLibrarySeries(element) {
  const detail = state.seriesDetail;
  if (!detail) return;
  const tmdb = Number(element.dataset.tmdb);
  const season = element.dataset.season ? Number(element.dataset.season) : null;
  const monitored = Boolean($('#modalBody input[name="monitored"]')?.checked);
  const selected = season
    ? detail.seasons.filter((entry) => entry.season === season)
    : detail.seasons;
  const missingSeasons = selected
    .filter((entry) => entry.season > 0)
    .filter((entry) => (entry.episodes || []).some((episode) => !episode.in_catalog))
    .map((entry) => entry.season);
  try {
    if (missingSeasons.length) {
      await apiPost('/api/titles', { kind: 'series', tmdb, seasons: missingSeasons });
    }
    const result = await apiPost(`/api/series/${tmdb}/library`, {
      member: true,
      monitored,
      ...(season !== null ? { seasons: [season] } : {}),
    });
    const mode = element.dataset.mode || 'add';
    const done = mode === 'add'
      ? (season !== null ? 'Temporada añadida' : 'Serie añadida')
      : 'Episodios añadidos';
    const doneMonitored = `${done} y en seguimiento`;
    const queued = result.job_id ? `, búsqueda en cola (#${result.job_id})` : '';
    closeModal();
    toast(
      `${monitored ? doneMonitored : done}${queued}`,
      monitored ? 'warn' : 'ok',
      result.job_id
        ? `<button class="btn btn-sm btn-ghost" data-action="job-detail" data-id="${result.job_id}">Ver tarea</button>`
        : '',
    );
    await route();
    refreshActivity();
  } catch (error) {
    toast(error.message, 'error');
  }
}

/* --------------------------------------------------- búsqueda manual */

function manualReleaseRow(release) {
  const health = healthStatus(release.health);
  return `<tr>
    <td data-label="Versión"><div class="cell-title"><strong>${esc(release.title || '—')}</strong><small>${esc(release.tracker || '')}, #${release.id}</small></div></td>
    <td data-label="Calidad">${esc(release.quality || '—')}<div class="tiny faint">${esc(release.audio || '')}</div></td>
    <td data-label="Tamaño" class="num">${fmtBytes(release.size_bytes)}</td>
    <td data-label="Seeds" class="num">${fmtInt(release.seeders)} / ${fmtInt(release.peers)}</td>
    <td data-label="Salud">${status(health.label, health.tone)}</td>
    <td data-label="Privacidad">${esc(release.privacy === 'public' ? 'público' : 'privado')}</td>
    <td class="actions" data-label="">
      <button class="btn btn-sm btn-ghost" data-action="release-target" data-release="${release.id}">Fijar</button>
      <button class="btn btn-sm btn-ghost" data-action="release-health" data-release="${release.id}">Verificar</button>
      <button class="btn btn-sm btn-primary" data-action="release-prepare" data-release="${release.id}">Preparar</button>
    </td>
  </tr>`;
}

function manualSearchShell(id, query, content) {
  return `<form class="row" data-form="manual-search" data-id="${id}" style="margin-bottom:12px;align-items:flex-end">
      <label class="field grow"><span>Buscar por nombre</span>
        <input name="query" value="${esc(query)}" placeholder="Título alternativo, original o del tracker" autocomplete="off">
      </label>
      <button class="btn btn-primary" type="submit">Buscar</button>
    </form>
    ${content}`;
}

async function runManualSearch(id, query) {
  openModal(
    'Búsqueda manual',
    manualSearchShell(
      id,
      query,
      `<div class="row" style="gap:9px"><span class="spinner" aria-hidden="true"></span><span class="muted">Buscando en Prowlarr y Jackett…</span></div>`,
    ),
    `<button class="btn btn-ghost" data-action="modal-close">Cerrar</button>`,
    { wide: true },
  );
  try {
    const data = await apiPost(`/api/titles/${id}/manual-search`, {
      min_seeders: 0,
      query: query || '',
    });
    const releases = data.releases || [];
    const summary = data.summary || {};
    const rows = releases.map(manualReleaseRow).join('');
    const content = `
      <div class="row between" style="margin-bottom:10px">
        <span class="tiny muted">${fmtInt(summary.results || releases.length)} resultados, ${fmtInt(releases.length)} guardados</span>
        <span class="tiny faint">${esc(data.title || '')}</span>
      </div>
      ${data.forced ? `<div class="notice" style="margin-bottom:10px">Búsqueda forzada por nombre. Revisa que los resultados correspondan antes de preparar.</div>` : ''}
      ${rows ? `<div class="table-wrap"><table class="table--stack">
        <thead><tr><th>Versión</th><th>Calidad</th><th>Tamaño</th><th>Seeds</th><th>Salud</th><th>Privacidad</th><th></th></tr></thead>
        <tbody>${rows}</tbody>
      </table></div>` : `<div class="empty"><strong>Sin resultados</strong>Prueba otro nombre o busca el título original.</div>`}
      ${summary.errors?.length ? `<p class="notice" style="margin-top:10px">${esc(summary.errors.slice(0, 2).join(' / '))}</p>` : ''}`;
    openModal(
      'Búsqueda manual',
      manualSearchShell(id, data.query || query, content),
      `<button class="btn btn-ghost" data-action="manual-search-done">Cerrar y actualizar</button>
       <button class="btn btn-ghost" data-action="modal-close">Cerrar</button>`,
      { wide: true },
    );
  } catch (error) {
    openModal(
      'Búsqueda manual',
      manualSearchShell(id, query, `<div class="notice">${esc(error.message)}</div>`),
      `<button class="btn btn-ghost" data-action="modal-close">Cerrar</button>`,
      { wide: true },
    );
  }
}

async function openManualSearch(id, title = '') {
  const query = title || state.titleDetail?.item?.title || '';
  await runManualSearch(id, query);
}

/* --------------------------------------------------- fuentes manuales */

async function openAddSources(tmdb, title) {
  try {
    const detail = await api(`/api/series/${tmdb}`);
    state.seriesDetail = detail;
    const seasons = (detail.seasons || []).filter((season) => season.catalog_episodes > 0);
    if (!seasons.length) {
      toast('La serie no tiene episodios añadidos', 'warn');
      return;
    }
    openModal(
      `Buscar fuentes: ${title}`,
      `<p class="muted" style="margin-top:0">Elige temporada.</p>
       <div>
         ${seasons.map((season) => `<div class="kv">
           <span>${season.season === 0 ? 'Especiales' : `Temporada ${season.season}`} (${season.catalog_episodes} ${season.catalog_episodes === 1 ? 'episodio' : 'episodios'})</span>
           <span><button class="btn btn-sm btn-ghost" data-action="add-sync-season" data-season="${season.season}">Buscar fuentes</button></span>
         </div>`).join('')}
       </div>`,
      `<button class="btn btn-ghost" data-action="modal-close">Cerrar</button>
       <button class="btn btn-primary" data-action="add-sync-series">Buscar en toda la serie</button>`,
      { slim: true },
    );
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function syncAddSources(season = null) {
  const detail = state.seriesDetail;
  if (!detail) return;
  const selected = season === null
    ? detail.seasons
    : detail.seasons.filter((entry) => entry.season === Number(season));
  const episodes = selected
    .flatMap((entry) => entry.episodes || [])
    .filter((episode) => episode.in_catalog);
  if (!episodes.length) {
    toast('No hay episodios añadidos', 'warn');
    return;
  }
  try {
    const result = await apiPost(`/api/titles/${episodes[0].id}/sync`, {
      keys: episodes.map((episode) => episode.key),
    });
    closeModal();
    toast(`Búsqueda de fuentes en cola (#${result.job_id}) · ${episodes.length} episodios`, 'ok',
      `<a class="btn btn-sm btn-ghost" href="#/series/${detail.series.tmdb}">Ver serie</a>
       <button class="btn btn-sm btn-ghost" data-action="job-detail" data-id="${result.job_id}">Ver tarea</button>`);
    refreshActivity();
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function openSyncPending(publication) {  const routeInfo = parseRoute();
  const q = routeInfo.query.get('q') || '';
  const kind = routeInfo.query.get('kind') || '';
  try {
    const summary = await api(`/api/ondemand/pending-summary?publication=${encodeURIComponent(publication)}&kind=${encodeURIComponent(kind)}&q=${encodeURIComponent(q)}`);
    if (!summary.total) {
      toast('No hay títulos que buscar con esos filtros', 'warn');
      return;
    }
    const label = publication === 'pendiente'
      ? 'Buscar fuentes para los títulos que faltan en Jellyfin'
      : 'Buscar fuentes para los títulos sin fuente';
    openModal(
      label,
      `<p class="muted" style="margin-top:0">Se buscarán fuentes para <strong>${fmtInt(summary.movies)}</strong> películas y <strong>${fmtInt(summary.series_episodes)}</strong> episodios. En total, ${fmtInt(summary.total)} búsquedas.</p>
       <p class="muted tiny">No se descarga nada. Se puede cancelar en Tareas.</p>
       <form id="syncPendingForm" data-publication="${esc(publication)}">
         <div class="form-grid">
           <label class="field"><span>Límite (0 = todos)</span><input name="limit" type="number" min="0" value="0"></label>
           <label class="field"><span>Pausa entre búsquedas (s)</span><input name="delay" type="number" min="0" step="0.1" value="1"></label>
           <label class="field"><span>Mín. seeds</span><input name="min_seeders" type="number" min="0" value="1"></label>
         </div>
       </form>
       <div class="notice" style="margin-top:12px">Puede tardar horas. Usa un límite pequeño para probar.</div>`,
      `<button class="btn btn-ghost" data-action="modal-close">Cancelar</button>
       <button class="btn btn-primary" data-action="sync-pending-start" data-publication="${esc(publication)}">${icon('search')} Empezar búsqueda</button>`,
      { wide: true },
    );
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function startSyncPending(publication) {
  const form = $('#syncPendingForm');
  if (!form) return;
  const values = formValues(form);
  const routeInfo = parseRoute();
  try {
    const result = await apiPost('/api/ondemand/sync-pending', {
      publication,
      kind: routeInfo.query.get('kind') || '',
      q: routeInfo.query.get('q') || '',
      limit: values.limit || 0,
      delay: values.delay ?? 1,
      min_seeders: values.min_seeders ?? 1,
    });
    closeModal();
    toast(`Búsqueda de fuentes en cola (#${result.job_id})`, 'ok',
      `<button class="btn btn-sm btn-ghost" data-action="job-detail" data-id="${result.job_id}">Ver tarea</button>`);
    refreshActivity();
  } catch (error) {
    toast(error.message, 'error');
  }
}

/* ------------------------------------------------------------------- añadir */

async function addMovie(tmdb, title) {
  const ok = await confirmModal({
    title: 'Añadir película',
    message: `Se añadirá «${title}» (TMDB ${tmdb}).`,
    confirm: 'Añadir',
  });
  if (!ok) return;
  try {
    const result = await apiPost('/api/titles', { kind: 'movie', tmdb });
    if (result.key) await acquireKeys([result.key], 'movie', 'publish');
    await route();
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function addSeries(tmdb, title) {
  try {
    const data = await api(`/api/tmdb/seasons/${tmdb}`);
    const seasons = data.seasons || [];
    if (!seasons.length) { toast('TMDB no devolvió temporadas', 'warn'); return; }
    const latest = seasons[seasons.length - 1].season;
    openModal(
      `Añadir serie, ${title}`,
      `<p class="muted" style="margin-top:0">Elige las temporadas que quieres añadir. Se crearán todos sus episodios.</p>
       <div id="seasonsForm" class="chips" style="gap:8px">
         ${seasons.map((season) => `<label class="season-option chip" style="cursor:pointer">
           <input type="checkbox" name="season" value="${season.season}" ${season.season === latest ? 'checked' : ''}>
           <span>Temporada ${season.season} <b>${season.episodes} ep.</b></span>
         </label>`).join('')}
       </div>`,
      `<button class="btn btn-ghost" data-action="modal-close">Cancelar</button>
       <button class="btn btn-primary" data-action="confirm-add-series" data-tmdb="${tmdb}">Añadir seleccionadas</button>`,
      { wide: true },
    );
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function confirmAddSeries(tmdb) {
  const seasons = $$('#seasonsForm input[name="season"]:checked').map((el) => Number(el.value));
  if (!seasons.length) { toast('Elige al menos una temporada', 'warn'); return; }
  try {
    const result = await apiPost('/api/titles', { kind: 'series', tmdb, seasons });
    if (result.keys?.length) await acquireKeys(result.keys, 'series', 'publish');
    await route();
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function acquireKeys(keys, kind, action) {
  const result = await apiPost('/api/ondemand/acquire', { keys, kind, action });
  toast(
    `${action === 'publish' ? 'Añadido' : 'Descarga'} en cola (#${result.job_id}), ${keys.length} título(s)`,
    'ok',
    `<button class="btn btn-sm btn-ghost" data-action="job-detail" data-id="${result.job_id}">Ver tarea</button>`,
  );
  refreshActivity();
  return result;
}

async function expandSelection() {
  const keys = [];
  for (const value of state.selection) {
    if (value.startsWith('series:')) {
      const tmdb = Number(value.split(':')[1] || 0);
      if (!tmdb) continue;
      const detail = await api(`/api/series/${tmdb}`);
      for (const season of detail.seasons || []) {
        for (const episode of season.episodes || []) keys.push(episode.key);
      }
    } else {
      keys.push(value);
    }
  }
  return keys;
}

/* ------------------------------------------------------------------- login */

$('#loginForm')?.addEventListener('submit', async (event) => {
  event.preventDefault();
  const token = $('#loginToken').value;
  try {
    const response = await fetch('/api/login', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-Catalogo': 'web' },
      body: JSON.stringify({ token }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || 'token incorrecto');
    hideLogin();
    await boot();
  } catch (error) {
    $('#loginError').textContent = error.message;
  }
});

/* ------------------------------------------------------------------ eventos */

document.addEventListener('click', async (event) => {
  if (event.target.id === 'modal') { closeModal(); return; }
  if (event.target.id === 'palette') { closePalette(); return; }
  if (event.target.closest('[data-href]') && !event.target.closest('button, a, input, select, textarea')) {
    location.hash = event.target.closest('[data-href]').dataset.href;
    return;
  }
  if (event.target.closest('[data-preview]') && !event.target.closest('button, a, input, select, textarea')) {
    const preview = event.target.closest('[data-preview]');
    openTmdbPreview(preview.dataset.preview, preview.dataset.kind, preview.dataset.title);
    return;
  }
  if (event.target.id === 'scrim') {
    $('#sidebar').classList.remove('open');
    $('#scrim').classList.remove('open');
    return;
  }

  const element = event.target.closest('[data-action]');
  if (!element) return;
  const action = element.dataset.action;

  try {
    if (action === 'menu') {
      $('#sidebar').classList.toggle('open');
      $('#scrim').classList.toggle('open', $('#sidebar').classList.contains('open'));
      return;
    }
    if (action === 'menu-close') {
      $('#sidebar').classList.remove('open');
      $('#scrim').classList.remove('open');
      return;
    }
    if (action === 'modal-close') { closeModal(); return; }
    if (action === 'modal-confirm') { closeModal(true); return; }
    if (action === 'shortcuts') { openShortcuts(); return; }
    if (action === 'theme') { toggleTheme(); return; }
    if (action === 'attention-check') { await runAttentionCheck(); return; }
    if (action === 'refresh') { refreshActivity(); await route(); return; }
    if (action === 'logout') {
      await apiPost('/api/logout');
      showLogin();
      return;
    }
    if (action === 'palette-open') { openPalette(); return; }
    if (action === 'palette-close') { closePalette(); return; }
    if (action === 'palette-add') {
      const text = $('#paletteInput').value.trim();
      closePalette();
      navigate('add', [], { q: text });
      return;
    }
    if (action === 'detail-tab') {
      updateQuery({ tab: element.dataset.tab });
      return;
    }
    if (action === 'catalog-filter') {
      updateQuery({ [element.dataset.key]: element.dataset.value });
      return;
    }
    if (action === 'catalog-incomplete') {
      const current = parseRoute().query;
      updateQuery({ incomplete: current.get('incomplete') === '1' ? '' : '1' });
      return;
    }
    if (action === 'catalog-select-all') {
      const boxes = $$('#view [data-action="catalog-select"]');
      const allSelected = boxes.length > 0
        && boxes.every((box) => state.selection.has(box.dataset.key));
      for (const box of boxes) {
        const key = box.dataset.key;
        if (allSelected) state.selection.delete(key);
        else state.selection.add(key);
        box.checked = !allSelected;
        box.closest('.media-card, .media-row, .catalog-table tr')?.classList.toggle('selected', !allSelected);
      }
      renderSelectionBar();
      return;
    }
    if (action === 'calendar-days') {
      updateQuery({ days: element.dataset.days }, { resetPage: false });
      return;
    }
    if (action === 'catalog-view') {
      updateQuery({ view: element.dataset.view });
      return;
    }
    if (action === 'toggle-filters') {
      state.filtersOpen = !state.filtersOpen;
      await route();
      return;
    }
    if (action === 'sync-pending-open') {
      await openSyncPending(element.dataset.publication);
      return;
    }
    if (action === 'sync-pending-start') {
      await startSyncPending(element.dataset.publication);
      return;
    }
    if (action === 'catalog-clear') {
      clearCatalogSelectionUI();
      navigate('catalog', [], {});
      return;
    }
    if (action === 'catalog-page') {
      updateQuery({ page: element.dataset.page });
      return;
    }
    if (action === 'technical-page') {
      updateQuery({ page: element.dataset.page });
      return;
    }
    if (action === 'downloads-filter') {
      updateQuery({ state: element.dataset.value });
      return;
    }
    if (action === 'series-season') {
      updateQuery({ season: element.dataset.season }, { resetPage: false });
      return;
    }
    if (action === 'add-result') {
      await addSearchResult(element);
      return;
    }
    if (action === 'torrent-pick') {
      $('#torrentFile')?.click();
      return;
    }
    if (action === 'save-limit') {
      const value = Number($('input[name="max_active"]')?.value || 0);
      const result = await apiPost('/api/ondemand/settings', { max_active: value });
      toast(`Límite on-demand actualizado a ${result.max_active}`, 'ok');
      return;
    }
    if (action === 'settings-jump') {
      const target = document.getElementById(element.dataset.target);
      if (target) {
        const smooth = !window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        target.scrollIntoView({ behavior: smooth ? 'smooth' : 'auto', block: 'start' });
      }
      return;
    }
    if (action === 'language-save') {
      await apiPost('/api/language', {
        preference: $('[data-language]')?.value || 'prefer_spanish',
      });
      state.settingsDirty = false;
      toast('Preferencia de idioma actualizada', 'ok');
      await route({ preserveScroll: true });
      return;
    }
    if (action === 'seed-save') {
      const values = {};
      $$('[data-seed-field]').forEach((input) => {
        const key = input.dataset.seedField;
        values[key] = input.type === 'checkbox' ? input.checked : input.value;
      });
      if (values.min_days !== undefined) {
        values.min_hours = Number(values.min_days) * 24;
        delete values.min_days;
      }
      await apiPost('/api/seed', values);
      state.settingsDirty = false;
      toast('Siembra actualizada', 'ok');
      await route({ preserveScroll: true });
      return;
    }
    if (action === 'seed-policy-save') {
      const tracker = element.dataset.tracker;
      const values = { tracker };
      $$(`[data-seed-tracker="${CSS.escape(tracker)}"]`).forEach((input) => {
        const key = input.dataset.seedField;
        values[key] = input.type === 'checkbox' ? input.checked : input.value;
      });
      if (values.min_days !== undefined) {
        values.min_seed_hours = Number(values.min_days) * 24;
        delete values.min_days;
      }
      await apiPost('/api/seed/policies', values);
      toast(`Política de ${tracker} actualizada`, 'ok');
      await route({ preserveScroll: true });
      return;
    }
    if (action === 'settings-save') {
      const values = {};
      $$('[data-setting]').forEach((input) => {
        const key = input.dataset.setting;
        if (!key) return;
        if (input.type === 'password' && !input.value) return;
        values[key] = input.value;
      });
      const result = await apiPost('/api/settings', { values });
      state.settingsDirty = false;
      $('#settingsDirty')?.classList.add('hidden');
      const restart = result.restart_labels || [];
      toast(
        restart.length
          ? `Guardado. Requiere reinicio: ${restart.join(', ')}`
          : 'Cambios guardados',
        restart.length ? 'warn' : 'ok',
      );
      await route({ preserveScroll: true });
      return;
    }
    if (action === 'settings-clear') {
      const key = element.dataset.setting;
      const ok = await confirmModal({
        title: 'Borrar ajuste',
        message: `Se borrará el valor guardado de ${key}. Volverá el valor del entorno o el de por defecto.`,
        confirm: 'Borrar',
        danger: true,
      });
      if (!ok) return;
      await apiPost('/api/settings', { clear: [key] });
      toast('Ajuste borrado', 'ok');
      await route({ preserveScroll: true });
      return;
    }
    if (action === 'settings-test') {
      const name = element.dataset.name;
      const previous = element.innerHTML;
      element.disabled = true;
      element.textContent = 'Probando…';
      const values = {};
      $$('[data-setting]').forEach((input) => {
        const key = input.dataset.setting;
        if (!key) return;
        if (input.type === 'password' && !input.value) return;
        values[key] = input.value;
      });
      try {
        const result = await apiPost(`/api/integrations/${name}/test`, { values });
        const target = $(`[data-integration-result="${CSS.escape(name)}"]`);
        if (target) {
          target.textContent = result.ok
            ? `Correcto · ${result.detail || ''} (${result.latency_ms || 0} ms)`
            : `Error · ${result.hint || result.error || 'sin detalle'}`;
          target.className = `tiny ${result.ok ? 'ok-text' : 'warn-text'}`;
        }
        toast(
          result.ok
            ? `${name}: conexión correcta`
            : `${name}: ${result.hint || result.error || 'error'}`,
          result.ok ? 'ok' : 'error',
        );
      } finally {
        element.disabled = false;
        element.innerHTML = previous;
      }
      return;
    }
    if (action === 'settings-path') {
      const key = element.dataset.setting;
      const input = $(`[data-setting="${CSS.escape(key)}"]`);
      const result = await apiPost('/api/settings/path-check', { key, path: input?.value || '' });
      const target = $(`[data-path-for="${CSS.escape(key)}"]`);
      if (target) {
        const creatable = ['catalog_library', 'downloads_root', 'seed_root', 'ondemand_save_path'].includes(key);
        const parent = result.checked_parent || '—';
        if (result.exists) {
          const state = result.is_dir
            ? (result.writable ? 'Directorio, escribible' : 'Directorio, sin escritura')
            : 'No es un directorio';
          target.textContent = `${state} · ${fmtBytes(result.free)} libres`;
        } else if (!result.writable) {
          target.textContent = `No existe y la carpeta padre ${parent} no permite escribir.`;
        } else if (creatable) {
          target.textContent = `No existe; se creará dentro de ${parent} · ${fmtBytes(result.free || 0)} libres.`;
        } else {
          target.textContent = `No existe. La carpeta padre ${parent} existe (${fmtBytes(result.free || 0)} libres), pero esta ruta no. Créala o corrige la ruta.`;
        }
        target.className = `tiny ${result.exists && result.writable ? 'ok-text' : 'warn-text'} path-status`;
      }
      return;
    }
    if (action === 'scheduler-save') {
      const task = element.dataset.task;
      const body = {};
      $$(`[data-task="${CSS.escape(task)}"][data-field]`).forEach((input) => {
        const field = input.dataset.field;
        if (field === 'enabled') body.enabled = input.checked;
        else if (field === 'interval') body.interval = Number(input.value);
        else body[field] = input.value;
      });
      const params = {};
      $$(`[data-task="${CSS.escape(task)}"][data-param]`).forEach((input) => {
        const key = input.dataset.param;
        params[key] = input.type === 'checkbox'
          ? input.checked
          : input.type === 'number' ? Number(input.value) : input.value;
      });
      if (Object.keys(params).length) body.params = params;
      await apiPost(`/api/scheduler/${task}`, body);
      state.settingsDirty = false;
      toast('Tarea actualizada', 'ok');
      await route({ preserveScroll: true });
      return;
    }
    if (action === 'scheduler-run') {
      const task = element.dataset.task;
      const result = await apiPost(`/api/scheduler/${task}/run`, {});
      toast(`Tarea lanzada (#${result.job_id})`, 'ok', `<a class="btn btn-sm btn-ghost" href="#/jobs">Ver historial</a>`);
      return;
    }
    if (action === 'catalog-select') {
      const selected = toggleSelection(element.dataset.key);
      const card = element.closest('.media-card, .media-row, .catalog-table tr');
      if (card) card.classList.toggle('selected', selected);
      renderSelectionBar();
      return;
    }
    if (action === 'bulk-clear') {
      clearCatalogSelectionUI();
      return;
    }
    if (action === 'torrent-select') {
      const hash = element.dataset.hash;
      if (state.torrentSelection.has(hash)) state.torrentSelection.delete(hash);
      else state.torrentSelection.add(hash);
      renderSelectionBar();
      return;
    }
    if (action === 'torrent-clear') {
      clearTorrentSelectionUI();
      return;
    }
    if (action === 'bulk-publish' || action === 'bulk-download') {
      if (!state.selection.size) return;
      const publish = action === 'bulk-publish';
      const keys = await expandSelection();
      if (!keys.length) { toast('No hay claves que procesar', 'warn'); return; }
      const ok = await confirmModal({
        title: publish ? 'Añadir' : 'Descargar ahora',
        message: publish
          ? `Se añadirán ${keys.length} título(s).`
          : `Se descargarán ${keys.length} título(s). Puede tardar y saltarse la cola.`,
        confirm: publish ? 'Añadir' : 'Descargar ahora',
        danger: !publish,
      });
      if (!ok) return;
      await acquireKeys(keys, keys.some((key) => key.startsWith('series:')) ? 'series' : 'movie', publish ? 'publish' : 'download');
      clearCatalogSelectionUI();
      return;
    }
    if (action === 'monitor-filter') {
      updateQuery({ state: element.dataset.value }, { resetPage: false });
      return;
    }
    if (action === 'monitor-page') {
      updateQuery({ page: element.dataset.page }, { resetPage: false });
      return;
    }
    if (action === 'monitor-refresh') {
      await route();
      return;
    }
    if (action === 'monitor-indexer') {
      const enabled = element.dataset.enabled === '1';
      const next = !enabled;
      const ok = await confirmModal({
        title: next ? 'Activar indexer' : 'Pausar indexer',
        message: next
          ? 'El indexer volverá a usarse en Prowlarr. Afecta también a Radarr y Sonarr.'
          : 'El indexer dejará de usarse en Prowlarr. Afecta también a Radarr y Sonarr.',
        confirm: next ? 'Activar' : 'Pausar',
        danger: !next,
      });
      if (!ok) return;
      element.disabled = true;
      try {
        await apiPost(`/api/monitor/indexers/${element.dataset.id}`, { enabled: next });
        toast(next ? 'Indexer activado' : 'Indexer pausado', 'ok');
        await route();
      } catch (error) {
        toast(error.message, 'error');
      } finally {
        element.disabled = false;
      }
      return;
    }
    if (action === 'monitor-search') {
      element.disabled = true;
      try {
        const result = await apiPost('/api/jobs', {
          kind: 'monitor',
          params: { keys: [element.dataset.key], limit: 1, delay: 0, force: true },
        });
        toast(`Búsqueda en cola (#${result.job_id})`, 'ok',
          `<button class="btn btn-sm btn-ghost" data-action="job-detail" data-id="${result.job_id}">Ver tarea</button>`);
        refreshActivity();
      } catch (error) {
        toast(error.message, 'error');
      } finally {
        element.disabled = false;
      }
      return;
    }
    if (action === 'monitor-run') {
      const missing = Number(state.monitorSummary?.missing || 0);
      if (!missing) {
        toast('No hay títulos disponibles pendientes de buscar', 'warn');
        return;
      }
      const ok = await confirmModal({
        title: 'Buscar seguimiento',
        message: `Se buscarán ${fmtInt(missing)} títulos disponibles. Los pendientes de estreno se omiten.`,
        confirm: 'Buscar',
      });
      if (!ok) return;
      element.disabled = true;
      try {
        const result = await apiPost('/api/jobs', {
          kind: 'monitor',
          params: { limit: 100, delay: 1 },
        });
        toast(`Búsqueda en cola (#${result.job_id})`, 'ok',
          `<button class="btn btn-sm btn-ghost" data-action="job-detail" data-id="${result.job_id}">Ver tarea</button>`);
        refreshActivity();
      } catch (error) {
        toast(error.message, 'error');
      } finally {
        element.disabled = false;
      }
      return;
    }
    if (action === 'library-series' || action === 'library-season') {
      openLibrarySeries(
        Number(element.dataset.tmdb),
        element.dataset.title || '',
        action === 'library-season' ? Number(element.dataset.season) : null,
        element.dataset.mode || 'add',
      );
      return;
    }
    if (action === 'library-series-start') {
      await startLibrarySeries(element);
      return;
    }
    if (action === 'library-title') {
      openLibraryTitle(
        element.dataset.id,
        element.dataset.key,
        element.dataset.title || '',
        element.dataset.releases || 0,
      );
      return;
    }
    if (action === 'library-title-start') {
      await startLibraryTitle(element);
      return;
    }
    if (action === 'monitor-title') {
      element.disabled = true;
      try {
        const result = await apiPost(`/api/titles/${element.dataset.id}/monitor`, { monitored: true });
        toast(
          result.job_id
            ? `En seguimiento · búsqueda en cola (#${result.job_id})`
            : 'En seguimiento hasta encontrar fuente',
          'ok',
          result.job_id
            ? `<button class="btn btn-sm btn-ghost" data-action="job-detail" data-id="${result.job_id}">Ver tarea</button>`
            : '',
        );
        await route();
      } finally {
        element.disabled = false;
      }
      return;
    }
    if (action === 'unmonitor-title') {
      element.disabled = true;
      try {
        await apiPost(`/api/titles/${element.dataset.id}/monitor`, { monitored: false });
        toast('Seguimiento desactivado', 'ok');
        await route();
      } finally {
        element.disabled = false;
      }
      return;
    }
    if (action === 'publish-title') {
      await acquireKeys([element.dataset.key], element.dataset.key.startsWith('series:') ? 'series' : 'movie', 'publish');
      await route();
      return;
    }
    if (action === 'unpublish-title') {
      const ok = await confirmModal({
        title: 'Quitar de TOD',
        message: 'Se quitará este título de TOD. No se borran las descargas ni los torrents.',
        confirm: 'Quitar',
        danger: true,
      });
      if (!ok) return;
      element.disabled = true;
      try {
        const result = await apiPost(`/api/titles/${element.dataset.id}/unpublish`, {});
        toast(
          result.removed ? 'Quitado de TOD' : 'No había nada que quitar',
          result.removed ? 'ok' : 'warn',
        );
        await route();
      } finally {
        element.disabled = false;
      }
      return;
    }
    if (action === 'unpublish-series') {
      const ok = await confirmModal({
        title: 'Quitar serie de TOD',
        message: 'Se quitará esta serie de TOD. No se borran las descargas ni los torrents.',
        confirm: 'Quitar',
        danger: true,
      });
      if (!ok) return;
      element.disabled = true;
      try {
        const result = await apiPost(`/api/series/${element.dataset.tmdb}/unpublish`, {});
        toast(
          result.removed ? 'Serie quitada de TOD' : 'No había nada que quitar',
          result.removed ? 'ok' : 'warn',
        );
        await route();
      } finally {
        element.disabled = false;
      }
      return;
    }
    if (action === 'download-now') {
      const ok = await confirmModal({
        title: 'Descargar ahora',
        message: 'Se buscarán fuentes si hace falta y se forzará la descarga: no esperará a la cola de Radarr/Sonarr ni al límite on-demand.',
        confirm: 'Descargar ahora',
        danger: true,
      });
      if (!ok) return;
      element.disabled = true;
      try {
        await acquireKeys([element.dataset.key], element.dataset.key.startsWith('series:') ? 'series' : 'movie', 'download');
      } finally {
        element.disabled = false;
      }
      return;
    }
    if (action === 'collection-add') {
      const mode = element.dataset.mode === 'library' ? 'library' : 'catalog';
      const ok = await confirmModal({
        title: mode === 'library' ? 'Añadir colección' : 'Añadir sin descargar',
        message: mode === 'library'
          ? 'Se añadirán las películas que falten. Puede tardar.'
          : 'Se añadirán las películas sin descargarlas.',
        confirm: mode === 'library' ? 'Añadir colección' : 'Añadir sin descargar',
      });
      if (!ok) return;
      element.disabled = true;
      try {
        const result = await apiPost(`/api/collections/${element.dataset.id}/add`, { action: mode });
        const skipped = Number(result.skipped_no_source || 0);
        const monitored = Number(result.monitored || 0);
        if (mode === 'library' && !result.job_id && !monitored) {
          toast('No se añadió ninguna: sin fuente', 'warn');
        } else if (mode === 'library' && !result.job_id) {
          toast(
            monitored
              ? `${fmtInt(monitored)} en seguimiento hasta encontrar fuente`
              : 'Toda la colección ya está añadida',
            monitored ? 'warn' : 'ok',
          );
        } else {
          toast(
            `${fmtInt(result.imported || 0)} importadas, ${fmtInt(result.existing || 0)} ya estaban${monitored ? `, ${fmtInt(monitored)} en seguimiento` : ''}`,
            monitored ? 'warn' : 'ok',
            result.job_id
              ? `<button class="btn btn-sm btn-ghost" data-action="job-detail" data-id="${result.job_id}">Ver tarea</button>`
              : '',
          );
        }
        if (result.errors?.length) {
          toast(`La colección terminó con ${result.errors.length} aviso(s)`, 'warn');
        }
        await route();
        refreshActivity();
      } finally {
        element.disabled = false;
      }
      return;
    }
    if (action === 'add-movie') {
      await addMovie(Number(element.dataset.tmdb), element.dataset.title || '');
      return;
    }
    if (action === 'add-sources') {
      await openAddSources(Number(element.dataset.tmdb), element.dataset.title || 'Serie');
      return;
    }
    if (action === 'add-sync-series') {
      await syncAddSources(null);
      return;
    }
    if (action === 'add-sync-season') {
      await syncAddSources(Number(element.dataset.season));
      return;
    }
    if (action === 'add-series') {
      await addSeries(Number(element.dataset.tmdb), element.dataset.title || '');
      return;
    }
    if (action === 'confirm-add-series') {
      await confirmAddSeries(Number(element.dataset.tmdb));
      return;
    }
    if (action === 'manual-search') {
      await openManualSearch(element.dataset.id, element.dataset.title || '');
      return;
    }
    if (action === 'manual-search-done') {
      closeModal();
      await route();
      return;
    }
    if (action === 'sync-title' || action === 'quick-sync') {
      element.disabled = true;
      try {
        const result = await apiPost(`/api/titles/${element.dataset.id}/sync`, {});
        toast(`Búsqueda de fuentes en cola (#${result.job_id})`, 'ok',
          `<a class="btn btn-sm btn-ghost" href="#/title/${element.dataset.id}?tab=fuentes">Ver ficha</a>
           <button class="btn btn-sm btn-ghost" data-action="job-detail" data-id="${result.job_id}">Ver tarea</button>`);
        refreshActivity();
      } finally {
        element.disabled = false;
      }
      return;
    }
    if (action === 'manual-search-season') {
      const detail = state.seriesDetail;
      if (!detail) return;
      const seasonNumber = Number(element.dataset.season || 0);
      const seasonEntry = (detail.seasons || []).find(
        (entry) => Number(entry.season) === seasonNumber,
      );
      const episodes = (seasonEntry?.episodes || []).filter(
        (episode) => episode.in_catalog,
      );
      if (!episodes.length) {
        toast('No hay episodios añadidos en esta temporada', 'warn');
        return;
      }
      openModal(
        `Buscar manualmente, temporada ${seasonNumber}`,
        `<p class="muted" style="margin-top:0">Elige el episodio.</p>
         <div class="stack">
           ${episodes.map((episode) => {
             const tag = `T${String(episode.season || 0).padStart(2, '0')}E${String(episode.episode || 0).padStart(2, '0')}`;
             const name = episode.name || `Episodio ${episode.episode}`;
             return `<button class="btn btn-sm" data-action="manual-search" data-id="${episode.id}" data-title="${esc(name)}">${tag} · ${esc(name)}</button>`;
           }).join('')}
         </div>`,
        `<button class="btn btn-ghost" data-action="modal-close">Cerrar</button>`,
        { wide: true },
      );
      return;
    }
    if (action === 'sync-series' || action === 'sync-season') {
      const detail = state.seriesDetail;
      if (!detail) return;
      const selected = action === 'sync-series'
        ? detail.seasons
        : detail.seasons.filter((season) => season.season === Number(element.dataset.season));
      const keys = selected
        .flatMap((season) => season.episodes || [])
        .filter((episode) => episode.in_catalog)
        .map((episode) => episode.key);
      const first = selected
        .flatMap((season) => season.episodes || [])
        .find((episode) => episode.in_catalog)?.id;
      if (!first || !keys.length) {
        toast('No hay episodios añadidos para buscar fuentes', 'warn');
        return;
      }
      const result = await apiPost(`/api/titles/${first}/sync`, { keys });
      toast(`Búsqueda de fuentes en cola (#${result.job_id}), ${keys.length} episodios`, 'ok');
      refreshActivity();
      return;
    }
    if (action === 'release-target') {
      const result = await apiPost(`/api/releases/${element.dataset.release}/target`, { source_kind: 'torrent' });
      toast(`Versión fijada para ${result.key}`, 'ok');
      await route();
      return;
    }
    if (action === 'release-auto') {
      await apiPost(`/api/releases/${element.dataset.release}/target`, { source_kind: 'auto' });
      toast('Puntero vuelto a automático', 'ok');
      await route();
      return;
    }
    if (action === 'release-health') {
      const result = await apiPost(`/api/releases/${element.dataset.release}/health`, {});
      toast(`Verificación en cola (#${result.job_id})`, 'ok');
      refreshActivity();
      return;
    }
    if (action === 'release-prepare') {
      const ok = await confirmModal({
        title: 'Preparar descarga',
        message: 'Se creará un compromiso de seed y se añadirá el torrent a qBittorrent bajo admisión. La descarga puede tardar según los peers.',
        confirm: 'Preparar',
      });
      if (!ok) return;
      element.disabled = true;
      try {
        const result = await apiPost(`/api/releases/${element.dataset.release}/prepare`, {});
        openModal('Descarga preparada',
          `<p>Torrent añadido con hash <span class="mono">${esc(result.info_hash || '—')}</span>.</p>
           <p class="muted tiny">Sigue el progreso en Descargas o en el estado del título.</p>`,
          `<button class="btn btn-ghost" data-action="modal-close">Cerrar</button>
           <button class="btn btn-primary" data-action="go-title" data-id="${state.params[0]}">Ver ficha</button>`);
      } finally {
        element.disabled = false;
      }
      return;
    }
    if (action === 'qbit') {
      await apiPost(`/api/downloads/${element.dataset.hash}/action`, { action: element.dataset.qaction });
      toast(`Torrent ${element.dataset.hash.slice(0, 10)}… actualizado`, 'ok');
      await route();
      return;
    }
    if (action === 'qbit-delete') {
      const hash = element.dataset.hash;
      openModal(
        'Borrar torrent',
        `<p class="muted" style="margin-top:0">Elige si quieres quitar solo el torrent de qBittorrent o borrar también sus ficheros. Esta acción no se puede deshacer.</p>
         <div class="notice">En trackers privados, borrar ficheros puede incumplir el mínimo de seed.</div>`,
        `<button class="btn btn-ghost" data-action="modal-close">Cancelar</button>
         <button class="btn" data-action="qbit-delete-run" data-hash="${esc(hash)}" data-delete-files="0">Quitar torrent</button>
         <button class="btn btn-danger" data-action="qbit-delete-run" data-hash="${esc(hash)}" data-delete-files="1">Borrar con ficheros</button>`,
        { slim: true },
      );
      return;
    }
    if (action === 'qbit-delete-run') {
      const hash = element.dataset.hash;
      const deleteFiles = element.dataset.deleteFiles === '1';
      element.disabled = true;
      try {
        await apiPost(`/api/downloads/${hash}/action`, {
          action: 'delete',
          delete_files: deleteFiles,
        });
        state.torrentSelection.delete(hash);
        toast(deleteFiles ? 'Torrent y ficheros borrados' : 'Torrent quitado', 'ok');
        closeModal();
        await route();
      } catch (error) {
        toast(error.message, 'error');
      } finally {
        element.disabled = false;
      }
      return;
    }
    if (action === 'qbit-bulk') {
      const hashes = [...state.torrentSelection];
      if (!hashes.length) return;
      const ok = await confirmModal({
        title: element.dataset.qaction === 'pause' ? 'Pausar torrents' : 'Reanudar torrents',
        message: `Se aplicará a ${hashes.length} torrent(s) de TOD.`,
        confirm: 'Aplicar',
      });
      if (!ok) return;
      for (const hash of hashes) {
        await apiPost(`/api/downloads/${hash}/action`, { action: element.dataset.qaction });
      }
      clearTorrentSelectionUI();
      toast(`${hashes.length} torrent(s) actualizados`, 'ok');
      await route();
      return;
    }
    if (action === 'seed-finish') {
      const result = await apiPost(`/api/seeds/${element.dataset.id}/finish`, {});
      if (result.closed) toast('Mínimo cumplido; qBittorrent decide desde ahora', 'ok');
      else toast(`Aún no se puede soltar: ${result.reason || 'política sin cumplir'}`, 'warn');
      await route();
      return;
    }
    if (action === 'open-task') { openTask(element.dataset.kind); return; }
    if (action === 'submit-task') { await submitTask(element.dataset.kind); return; }
    if (action === 'job-detail') { await showJob(element.dataset.id); return; }
    if (action === 'job-cancel') {
      await apiPost(`/api/jobs/${element.dataset.id}/cancel`, {});
      closeModal();
      toast('Cancelación solicitada', 'ok');
      refreshActivity();
      if (state.view === 'jobs') await route();
      return;
    }
    if (action === 'tracker-block') {
      const blocked = element.dataset.blocked === '1';
      const tracker = element.dataset.tracker;
      if (!blocked) {
        const ok = await confirmModal({
          title: 'Desbloquear tracker',
          message: `Se devolverán los releases de ${tracker} a estado desconocido y se habilitará su política.`,
          confirm: 'Desbloquear',
        });
        if (!ok) return;
        const result = await apiPost('/api/trackers/block', {
          tracker, blocked: false,
        });
        toast(`${tracker}: ${result.releases} releases afectados`, 'ok');
        await route({ preserveScroll: true });
        return;
      }

      const choice = await new Promise((resolve) => {
        openModal(
          'Bloquear tracker',
          `<p class="muted" style="margin-top:0">Se marcarán todos los releases de <strong>${esc(tracker)}</strong> como bloqueados y se deshabilitará su política. No se tocan las descargas activas.</p>
           <label class="inline check" style="margin-top:10px">
             <input type="checkbox" id="trackerDeleteStrm">
             Eliminar también los .strm de este tracker
           </label>
           <p class="tiny faint" style="margin:6px 0 0">Se borrarán las versiones que apuntan a sus releases. El .strm principal solo se elimina si el título se queda sin otra fuente ni fichero local.</p>`,
          `<button class="btn btn-ghost" data-action="modal-close">Cancelar</button>
           <button class="btn btn-danger" data-action="tracker-block-start" data-tracker="${esc(tracker)}">Bloquear</button>`,
          { slim: true, resolve },
        );
      });
      if (!choice) return;

      const result = await apiPost('/api/trackers/block', {
        tracker,
        blocked: true,
        delete_strm: Boolean(choice.delete_strm),
      });
      const removed = Number(result.strm?.total || 0);
      toast(
        `${tracker}: ${result.releases} releases bloqueados${removed ? ` · ${removed} .strm ${removed === 1 ? 'eliminado' : 'eliminados'}` : ''}`,
        'ok',
      );
      await route({ preserveScroll: true });
      return;
    }
    if (action === 'tracker-block-start') {
      const deleteStrm = Boolean($('#trackerDeleteStrm')?.checked);
      closeModal({ delete_strm: deleteStrm });
      return;
    }
    if (action === 'go-title') {
      closeModal();
      navigate('title', [element.dataset.id]);
      return;
    }
  } catch (error) {
    toast(error.message, 'error');
  }
});

document.addEventListener('change', (event) => {
  const target = event.target.closest('[data-change]');
  if (!target) return;
  if (target.dataset.change === 'catalog-sort') {
    updateQuery({ sort: target.value });
  } else if (target.dataset.change === 'monitor-kind') {
    updateQuery({ kind: target.value }, { resetPage: false });
  } else if (target.dataset.change === 'downloads-sort') {
    updateQuery({ sort: target.value });
  }
});

const debouncedCatalog = debounce((value) => updateQuery({ q: value }), 300);
const debouncedMonitor = debounce((value) => updateQuery({ q: value }), 300);
const debouncedDownloads = debounce((value) => updateQuery({ q: value }), 300);

document.addEventListener('input', (event) => {
  const input = event.target.closest('[data-input]');
  if (!input) return;
  const key = input.dataset.input;
  if (key === 'catalog-q') debouncedCatalog(input.value);
  else if (key === 'monitor-q') debouncedMonitor(input.value);
  else if (key === 'downloads-q') debouncedDownloads(input.value);
});

document.addEventListener('change', async (event) => {
  if (event.target.id !== 'torrentFile') return;
  await uploadTorrent(event.target.files?.[0]);
});

document.addEventListener('submit', async (event) => {
  const form = event.target;
  if (!(form instanceof HTMLFormElement)) return;
  if (!form.dataset.form) return;
  event.preventDefault();
  if (form.dataset.form === 'quick-search') {
    const values = formValues(form);
    navigate('add', [], { q: values.q || '' });
  } else if (form.dataset.form === 'add-submit') {
    await submitAdd(form);
  } else if (form.dataset.form === 'technical') {
    const values = formValues(form);
    navigate('technical', [], values);
  } else if (form.dataset.form === 'manual-search') {
    const values = formValues(form);
    await runManualSearch(form.dataset.id, values.query || '');
  }
});

$('#paletteForm')?.addEventListener('submit', (event) => {
  event.preventDefault();
  openActivePaletteItem();
});

$('#paletteInput')?.addEventListener('input', (event) => {
  const value = event.target.value;
  clearTimeout(paletteTimer);
  paletteTimer = setTimeout(() => searchPalette(value), 180);
});

document.addEventListener('keydown', (event) => {
  const paletteOpen = !$('#palette').classList.contains('hidden');
  if (paletteOpen) {
    if (event.key === 'Escape') { event.preventDefault(); closePalette(); return; }
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      if (!state.paletteItems.length) return;
      state.paletteIndex = (state.paletteIndex + (event.key === 'ArrowDown' ? 1 : -1) + state.paletteItems.length) % state.paletteItems.length;
      renderPalette();
      return;
    }
    if (event.key === 'Enter') {
      event.preventDefault();
      openActivePaletteItem();
      return;
    }
  }
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
    event.preventDefault();
    openPalette();
    return;
  }
  if (event.key === '?' && !['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement?.tagName)) {
    event.preventDefault();
    openShortcuts();
    return;
  }
  if (event.key === 'Escape') {
    if (!$('#modal').classList.contains('hidden')) { closeModal(); return; }
    if ($('#sidebar').classList.contains('open')) {
      $('#sidebar').classList.remove('open');
      $('#scrim').classList.remove('open');
    }
  }
  if ((event.key === 'Enter' || event.key === ' ') && document.activeElement?.matches('[data-href]')) {
    event.preventDefault();
    location.hash = document.activeElement.dataset.href;
    return;
  }
  if ((event.key === 'Enter' || event.key === ' ') && document.activeElement?.matches('[data-preview]')) {
    event.preventDefault();
    const preview = document.activeElement;
    openTmdbPreview(preview.dataset.preview, preview.dataset.kind, preview.dataset.title);
  }
});

window.addEventListener('hashchange', () => {
  if (!$('#palette').classList.contains('hidden')) closePalette();
  route();
});

onLiveChange(setLive);
onAuthRequired(showLogin);
window.addEventListener('route:rendered', renderSelectionBar);
window.addEventListener('beforeunload', (event) => {
  if (!state.settingsDirty) return;
  event.preventDefault();
  event.returnValue = '';
});
startActivityPolling();
startAttentionPolling();
boot();
