import { esc } from './format.js';
import { state } from './store.js';

export const $ = (selector, root = document) => root.querySelector(selector);
export const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

export function icon(name, cls = '') {
  return `<svg class="icon ${cls}" aria-hidden="true"><use href="#i-${name}"/></svg>`;
}

export function setLive(ok) {
  const dot = $('#liveDot');
  if (!dot) return;
  dot.classList.toggle('online', ok);
  dot.classList.toggle('offline', !ok);
  dot.title = ok ? 'Servidor conectado' : 'Servidor sin respuesta';
  dot.setAttribute('aria-label', ok ? 'Servidor conectado' : 'Servidor sin respuesta');
}

export function setHeader(title) {
  const titleEl = $('#pageTitle');
  if (titleEl) titleEl.textContent = title;
  document.title = `${title}, TOD`;
}

export function focusTitle() {
  const titleEl = $('#pageTitle');
  if (titleEl) titleEl.focus({ preventScroll: true });
}

export function focusKey() {
  const active = document.activeElement;
  if (!active) return null;
  if (active.dataset && active.dataset.input) return `[data-input="${CSS.escape(active.dataset.input)}"]`;
  if (active.id) return `#${CSS.escape(active.id)}`;
  return null;
}

export function restoreFocus(selector) {
  if (!selector) return false;
  let element = null;
  try { element = $(selector); } catch (_) { element = null; }
  if (!element) return false;
  element.focus({ preventScroll: true });
  if (typeof element.setSelectionRange === 'function' && typeof element.value === 'string') {
    const end = element.value.length;
    try { element.setSelectionRange(end, end); } catch (_) { /* no soportado */ }
  }
  return true;
}

export function status(label, tone = 'muted', extra = '') {
  return `<span class="status ${tone} ${extra}">${esc(label)}</span>`;
}

export function badge(label, cls = '') {
  return `<span class="badge ${cls}">${esc(label)}</span>`;
}

export function bar(value, tone = '') {
  const width = Math.max(0, Math.min(100, Math.round((Number(value) || 0) * 100)));
  return `<div class="bar ${tone}"><i style="width:${width}%"></i></div>`;
}

export function barPercent(value, tone = '') {
  const width = Math.max(0, Math.min(100, Number(value) || 0));
  return `<div class="bar ${tone}"><i style="width:${width}%"></i></div>`;
}

function tmdbImage(path, size) {
  if (/^https?:\/\/image\.tmdb\.org\/t\/p\//i.test(path)) {
    return path.replace(/(\/t\/p\/)[^/]+(\/)/, `$1${size}$2`);
  }
  if (/^https?:\/\//i.test(path)) return path;
  return `https://image.tmdb.org/t/p/${size}${path}`;
}

export function poster(path, title, { size = 'w342', cls = '', priority = false } = {}) {
  if (path) {
    const load = priority ? 'loading="eager" fetchpriority="high"' : 'loading="lazy"';
    return `<img src="${esc(tmdbImage(path, size))}" alt="" ${load} decoding="async">`;
  }
  const letter = String(title || '?').trim().slice(0, 1).toUpperCase();
  return `<span class="noimg" aria-hidden="true">${esc(letter)}</span>`;
}

export function thumb(path, title) {
  if (path) {
    return `<img class="thumb" src="${esc(tmdbImage(path, 'w92'))}" alt="" loading="lazy">`;
  }
  return `<span class="thumb placeholder">${esc(String(title || '?').trim().slice(0, 1).toUpperCase())}</span>`;
}

export function empty(title, text = '', action = '') {
  return `<div class="empty"><strong>${esc(title)}</strong>${text ? esc(text) : ''}${action ? `<div>${action}</div>` : ''}</div>`;
}

export function skeletonGrid(count = 12) {
  return `<div class="poster-grid">${Array.from({ length: count }, () => `
    <div class="stack">
      <div class="skeleton" style="aspect-ratio:2/3"></div>
      <div class="skeleton" style="height:14px;width:85%"></div>
      <div class="skeleton" style="height:11px;width:55%"></div>
    </div>`).join('')}</div>`;
}

export function pageError(error, retryAction = 'refresh') {
  return `<div class="panel pad"><div class="empty">
    <strong>No se pudo cargar</strong>
    <p class="muted">${esc(error && error.message ? error.message : String(error))}</p>
    <button class="btn" data-action="${esc(retryAction)}">${icon('refresh')} Reintentar</button>
  </div></div>`;
}

export function notice(text, tone = 'warn') {
  return `<div class="notice">${esc(text)}</div>`;
}

/* ------------------------------------------------------------------- toasts */

export function toast(message, kind = 'ok', actions = '', ttl = 0) {
  const box = $('#toasts');
  if (!box) return;
  const el = document.createElement('div');
  el.className = `toast ${kind}`;
  el.innerHTML = `${icon(kind === 'error' ? 'alert' : kind === 'warn' ? 'alert' : 'check')}
    <div class="txt">${esc(message)}${actions ? `<div class="actions">${actions}</div>` : ''}</div>
    <button class="btn btn-icon btn-sm" aria-label="Cerrar aviso">${icon('close')}</button>`;
  el.querySelector('button').addEventListener('click', () => el.remove());
  box.appendChild(el);
  const timeout = ttl || (kind === 'error' ? 9000 : kind === 'warn' ? 7000 : 4500);
  setTimeout(() => el.remove(), timeout);
}

/* -------------------------------------------------------------------- modal */

let modalResolve = null;
let modalFocus = null;
let trapHandler = null;

export function openModal(title, body, foot = '', options = {}) {
  modalFocus = document.activeElement;
  modalResolve = options.resolve || null;
  const modal = $('#modal');
  const card = $('.modal-card', modal);
  $('#modalTitle').textContent = title;
  $('#modalBody').innerHTML = body;
  $('#modalFoot').innerHTML = foot;
  card.classList.toggle('wide', Boolean(options.wide));
  card.classList.toggle('slim', Boolean(options.slim));
  modal.classList.remove('hidden');
  document.body.style.overflow = 'hidden';
  if (trapHandler) document.removeEventListener('keydown', trapHandler);
  trapHandler = (event) => {
    if (event.key !== 'Tab') return;
    const focusable = $$('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])', modal)
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
  document.addEventListener('keydown', trapHandler);
  const autofocus = $('[autofocus]', modal);
  (autofocus || focusableIn(modal) || $('.modal-head .btn', modal))?.focus();
}

function focusableIn(root) {
  return $$('button, input, select, textarea, [href]', root).find((el) => !el.disabled);
}

export function closeModal(value = false) {
  const modal = $('#modal');
  modal.classList.add('hidden');
  document.body.style.overflow = '';
  if (trapHandler) {
    document.removeEventListener('keydown', trapHandler);
    trapHandler = null;
  }
  if (modalFocus && typeof modalFocus.focus === 'function') {
    modalFocus.focus({ preventScroll: true });
  }
  modalFocus = null;
  const resolve = modalResolve;
  modalResolve = null;
  if (resolve) resolve(value);
}

export function settleModal(value) {
  closeModal(value);
}

export function confirmModal({
  title = 'Confirmar',
  message = '',
  confirm = 'Continuar',
  cancel = 'Cancelar',
  danger = false,
  details = '',
} = {}) {
  return new Promise((resolve) => {
    openModal(
      title,
      `<p class="muted" style="margin-top:0">${esc(message)}</p>${details ? `<div class="notice">${esc(details)}</div>` : ''}`,
      `<button class="btn btn-ghost" data-action="modal-close">${esc(cancel)}</button>
       <button class="btn ${danger ? 'btn-danger' : 'btn-primary'}" data-action="modal-confirm">${esc(confirm)}</button>`,
      { slim: true, resolve },
    );
  });
}

export function showResult(title, data, isError = false) {
  const text = typeof data === 'string' ? data : JSON.stringify(data, null, 2);
  openModal(
    title,
    `<pre class="result">${esc(text)}</pre>`,
    `<button class="btn btn-ghost" data-action="modal-close">Cerrar</button>`,
    { wide: true },
  );
  if (isError) toast('La operación devolvió un error', 'error');
}

/* ---------------------------------------------------------------- navegación */

const PRIMARY_ROUTE = {
  setup: null,
  dashboard: 'dashboard',
  add: 'add',
  intake: 'add',
  catalog: 'catalog',
  title: 'catalog',
  series: 'catalog',
  collection: 'catalog',
  monitor: 'catalog',
  calendar: 'catalog',
  downloads: 'downloads',
  jobs: 'downloads',
  history: 'downloads',
  seeds: 'downloads',
  blocklist: 'downloads',
  system: 'system',
  settings: 'system',
  technical: 'system',
};

const SECTION_FOR_VIEW = {
  catalog: 'catalog',
  title: 'catalog',
  series: 'catalog',
  collection: 'catalog',
  monitor: 'catalog',
  calendar: 'catalog',
  downloads: 'activity',
  jobs: 'activity',
  history: 'activity',
  seeds: 'activity',
  blocklist: 'activity',
  system: 'system',
  settings: 'system',
  technical: 'system',
};

const SECTION_TABS = {
  catalog: [
    { label: 'Biblioteca', href: '#/catalog', views: ['catalog', 'title', 'series', 'collection'] },
    { label: 'Buscados', href: '#/monitor', views: ['monitor'], count: 'pending' },
    { label: 'Calendario', href: '#/calendar', views: ['calendar'] },
  ],
  activity: [
    { label: 'Cola', href: '#/downloads', views: ['downloads'] },
    { label: 'Tareas', href: '#/jobs', views: ['jobs', 'history'], count: 'jobs' },
    { label: 'Siembra', href: '#/seeds', views: ['seeds'] },
    { label: 'Blocklist', href: '#/blocklist', views: ['blocklist'] },
  ],
  system: [
    { label: 'Estado', href: '#/system', views: ['system', 'technical'] },
    { label: 'Configuración', href: '#/settings', views: ['settings'] },
  ],
};

export function setSectionCount(kind, value) {
  const node = document.querySelector(`[data-tab-count="${kind}"]`);
  if (!node) return;
  node.textContent = value ? String(value) : '';
}

export function renderSectionTabs(view) {
  const box = document.getElementById('sectionTabs');
  if (!box) return;
  const section = SECTION_FOR_VIEW[view];
  const tabs = SECTION_TABS[section];
  if (!tabs) {
    box.innerHTML = '';
    box.classList.add('hidden');
    return;
  }
  box.classList.remove('hidden');
  box.dataset.section = section;
  box.innerHTML = tabs.map((tab) => {
    const active = tab.views.includes(view);
    return `<a href="${tab.href}" class="${active ? 'active' : ''}" ${active ? 'aria-current="page"' : ''}>
      ${esc(tab.label)}${tab.count ? `<em class="nav-count" data-tab-count="${tab.count}"></em>` : ''}
    </a>`;
  }).join('');
  setSectionCount('pending', state.activity?.monitored || 0);
  setSectionCount('jobs', (state.activity?.active || []).length);
}

export function setActiveNav(route) {
  const primary = Object.prototype.hasOwnProperty.call(PRIMARY_ROUTE, route)
    ? PRIMARY_ROUTE[route]
    : 'dashboard';
  $$('#nav a, #mobileNav a').forEach((link) => {
    const active = primary !== null && link.dataset.route === primary;
    link.classList.toggle('active', active);
    if (active) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  });
}
