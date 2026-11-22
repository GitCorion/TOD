import { esc } from './format.js';

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

export function setHeader(title, kicker = '') {
  const titleEl = $('#pageTitle');
  const kickerEl = $('#pageKicker');
  if (titleEl) titleEl.textContent = title;
  if (kickerEl) kickerEl.textContent = kicker;
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

export function poster(path, title, { size = 'w342', cls = '' } = {}) {
  if (path) {
    const url = /^https?:\/\//i.test(path)
      ? path
      : `https://image.tmdb.org/t/p/${size}${path}`;
    return `<img src="${esc(url)}" alt="" loading="lazy" decoding="async">`;
  }
  const letter = String(title || '?').trim().slice(0, 1).toUpperCase();
  return `<span class="noimg" aria-hidden="true">${esc(letter)}</span>`;
}

export function thumb(path, title) {
  if (path) {
    const url = /^https?:\/\//i.test(path)
      ? path
      : `https://image.tmdb.org/t/p/w92${path}`;
    return `<img class="thumb" src="${esc(url)}" alt="" loading="lazy">`;
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

export function setActiveNav(route) {
  $$('#nav a, #mobileNav a').forEach((link) => {
    const name = link.dataset.route;
    const active = name === route
      || (route === 'title' && name === 'catalog')
      || (route === 'collection' && name === 'catalog')
      || (route === 'series' && name === 'catalog')
      || (route === 'add' && name === 'catalog')
      || (route === 'history' && name === 'jobs')
      || (route === 'technical' && name === 'system');
    link.classList.toggle('active', active);
    if (active) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  });
}
