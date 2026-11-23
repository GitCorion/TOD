import { beginRequest, state, clearSelection } from './store.js';
import { $, setActiveNav, setHeader, renderSectionTabs, focusTitle, focusKey, restoreFocus, pageError } from './ui.js';
import { renderDashboard } from './views/dashboard.js';
import { renderCalendar } from './views/calendar.js';
import { renderHistory } from './views/history.js';
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
import { renderSetup } from './views/setup.js';

const ROUTES = {
  dashboard: { title: 'Inicio', render: renderDashboard },
  calendar: { title: 'Calendario', render: renderCalendar },
  catalog: { title: 'Biblioteca', render: renderCatalog },
  monitor: { title: 'Pendientes', render: renderMonitor },
  collection: { title: 'Saga', render: renderCollection },
  series: { title: 'Serie', render: renderSeries },
  title: { title: 'Ficha', render: renderTitle },
  add: { title: 'Añadir', render: renderAdd },
  setup: { title: 'Primeros pasos', render: renderSetup },
  intake: { title: 'Añadir', render: renderAdd },
  downloads: { title: 'Descargas', render: renderDownloads },
  jobs: { title: 'Tareas', render: renderJobs },
  history: { title: 'Historial', render: renderHistory },
  seeds: { title: 'Siembra', render: renderSeeds },
  settings: { title: 'Configuración', render: renderSettings },
  system: { title: 'Estado', render: renderSystem },
  technical: { title: 'Detalle técnico', render: renderTechnical },
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

export async function route({ preserveScroll = false } = {}) {
  const parsed = parseRoute();
  const keepScroll = preserveScroll && parsed.view === previousView;
  const scrollY = keepScroll ? window.scrollY : 0;
  if (
    state.settingsDirty
    && state.view === 'settings'
    && parsed.view !== 'settings'
    && !window.confirm('Hay cambios sin guardar. ¿Salir sin guardar?')
  ) {
    history.replaceState(null, '', buildHash('settings'));
    return;
  }
  if (parsed.view !== 'settings') state.settingsDirty = false;
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
  setHeader(definition.title);
  renderSectionTabs(parsed.view);
  $('#view').innerHTML = `<div class="view-loading" aria-busy="true" aria-label="Cargando">
    <div class="skeleton" style="height:34px;width:100%"></div>
    <div class="skeleton" style="height:28px;width:44%;margin-top:4px"></div>
    ${Array.from({ length: 7 }, () => '<div class="skeleton" style="height:38px;width:100%"></div>').join('')}
  </div>`;
  const signal = beginRequest();
  const previousFocus = focusKey();
  try {
    await definition.render({ ...parsed, signal });
    document.querySelector('#sidebar')?.classList.remove('open');
    document.querySelector('#scrim')?.classList.remove('open');
    if (keepScroll) {
      window.scrollTo({ top: scrollY, behavior: 'instant' });
    }
    if (!restoreFocus(previousFocus)) focusTitle();
    window.dispatchEvent(new CustomEvent('route:rendered'));
  } catch (error) {
    if (error.name === 'AbortError') return;
    $('#view').innerHTML = pageError(error);
  }
}
