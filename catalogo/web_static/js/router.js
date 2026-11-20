import { beginRequest, state, clearSelection } from './store.js';
import { $, setActiveNav, setHeader, focusTitle, focusKey, restoreFocus, pageError } from './ui.js';
import { renderDashboard } from './views/dashboard.js';
import { renderCatalog } from './views/catalog.js';
import { renderMonitor } from './views/monitor.js';
import { renderCollection } from './views/collection.js';
import { renderSeries } from './views/series.js';
import { renderTitle } from './views/title.js';
import { renderAdd } from './views/add.js';
import { renderDownloads } from './views/downloads.js';
import { renderJobs } from './views/jobs.js';
import { renderSeeds } from './views/seeds.js';
import { renderSettings } from './views/settings.js';
import { renderSystem } from './views/system.js';
import { renderTechnical } from './views/technical.js';
import { renderAdvanced } from './views/advanced.js';

const ROUTES = {
  dashboard: { title: 'Inicio', kicker: 'Biblioteca', render: renderDashboard },
  catalog: { title: 'Catálogo', kicker: 'Biblioteca', render: renderCatalog },
  monitor: { title: 'Seguimiento', kicker: 'Biblioteca', render: renderMonitor },
  collection: { title: 'Saga', kicker: 'Biblioteca', render: renderCollection },
  series: { title: 'Serie', kicker: 'Biblioteca', render: renderSeries },
  title: { title: 'Ficha', kicker: 'Biblioteca', render: renderTitle },
  add: { title: 'Añadir contenido', kicker: 'Biblioteca', render: renderAdd },
  downloads: { title: 'Descargas', kicker: 'Operación', render: renderDownloads },
  jobs: { title: 'Tareas', kicker: 'Operación', render: renderJobs },
  seeds: { title: 'Siembra', kicker: 'Operación', render: renderSeeds },
  settings: { title: 'Configuración', kicker: 'TOD', render: renderSettings },
  system: { title: 'Estado', kicker: 'Sistema', render: renderSystem },
  technical: { title: 'Catálogo técnico', kicker: 'Sistema', render: renderTechnical },
  advanced: { title: 'Avanzado', kicker: 'Sistema', render: renderAdvanced },
};

export function parseRoute() {
  const raw = (location.hash || '#/dashboard').replace(/^#\/?/, '');
  const [pathPart, queryPart = ''] = raw.split('?');
  const parts = pathPart.split('/').filter(Boolean);
  const view = parts[0] || 'dashboard';
  return {
    view,
    params: parts.slice(1),
    query: new URLSearchParams(queryPart),
    path: parts.join('/'),
  };
}

export function buildHash(view, params = [], query = {}) {
  const path = [view, ...params].filter(Boolean).join('/');
  const qs = new URLSearchParams();
  for (const [key, value] of Object.entries(query || {})) {
    if (value === undefined || value === null || value === '' || value === false) continue;
    qs.set(key, String(value));
  }
  const suffix = qs.toString();
  return `#/${path}${suffix ? `?${suffix}` : ''}`;
}

export function navigate(view, params = [], query = {}, { replace = false } = {}) {
  const target = buildHash(view, params, query);
  if (location.hash === target) {
    route();
    return;
  }
  if (replace) {
    history.replaceState(null, '', target);
    route();
  } else {
    location.hash = target;
  }
}

export function updateQuery(patch, { replace = true, resetPage = true } = {}) {
  const current = parseRoute();
  const query = Object.fromEntries(current.query.entries());
  if (resetPage) delete query.page;
  for (const [key, value] of Object.entries(patch || {})) {
    if (value === undefined || value === null || value === '' || value === false) delete query[key];
    else query[key] = value;
  }
  navigate(current.view, current.params, query, { replace });
}

let previousView = null;

export async function route() {
  const parsed = parseRoute();
  const definition = ROUTES[parsed.view] || ROUTES.dashboard;
  if (parsed.view !== previousView) {
    clearSelection();
    window.scrollTo({ top: 0, behavior: 'instant' });
  }
  previousView = parsed.view;
  state.view = parsed.view;
  state.params = parsed.params;
  state.query = parsed.query;
  setActiveNav(parsed.view);
  setHeader(definition.title, definition.kicker);
  $('#view').innerHTML = `<div class="panel pad"><div class="skeleton skeleton-block"></div><div class="skeleton" style="height:12px;width:40%;margin-top:12px"></div></div>`;
  const signal = beginRequest();
  const previousFocus = focusKey();
  try {
    await definition.render({ ...parsed, signal });
    document.querySelector('#sidebar')?.classList.remove('open');
    document.querySelector('#scrim')?.classList.remove('open');
    if (!restoreFocus(previousFocus)) focusTitle();
    window.dispatchEvent(new CustomEvent('route:rendered'));
  } catch (error) {
    if (error.name === 'AbortError') return;
    $('#view').innerHTML = pageError(error);
  }
}
