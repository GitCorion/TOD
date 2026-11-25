import { api } from '../api.js';
import { esc, fmtInt, fmtRelative, fmtShortDate, pct } from '../format.js';
import { updateQuery } from '../router.js';
import { state } from '../store.js';
import { $, badge, empty, icon, setHeader, status, thumb } from '../ui.js';

function stateInfo(item) {
  switch (item.state) {
    case 'pending':
      return status('Sin estrenar', 'muted');
    case 'downloading':
      return `${status('Descargando', 'info')} <span class="tiny faint">${pct(item.progress)}%</span>`;
    case 'queued':
      return status('En cola', 'warn');
    case 'waiting':
      return status('Por buscar', 'warn');
    case 'searching':
      return item.alive
        ? status('Fuente encontrada', 'info')
        : status('Buscando', 'warn');
    default:
      return status('Sin fuente', 'muted');
  }
}

function availability(item) {
  if (item.available !== false) return '<span class="tiny faint">Disponible</span>';
  return `<span class="tiny faint">${esc(fmtShortDate(item.available_at))}</span>`;
}

export async function renderMonitor({ query, signal }) {
  const tab = query.get('tab') || 'missing';
  const filters = {
    tab,
    q: query.get('q') || '',
    state: query.get('state') || '',
    kind: query.get('kind') || '',
    page: Math.max(1, Number(query.get('page')) || 1),
  };
  setHeader('Seguimiento (Wanted)');

  if (tab === 'cutoff') {
    const params = new URLSearchParams({ kind: filters.kind, limit: '200' });
    const data = await api(`/api/monitor/cutoff?${params}`, { signal });
    const items = (data.items || []).filter((it) => !filters.q || (it.title || '').toLowerCase().includes(filters.q.toLowerCase()));
    const rows = items.map((item) => {
      const label = item.kind === 'series'
        ? `T${String(item.season || 0).padStart(2, '0')}E${String(item.episode || 0).padStart(2, '0')}`
        : 'Película';
      const href = item.kind === 'series' ? `#/series/${item.tmdb}` : `#/title/${item.id}`;
      return `<tr>
        <td data-label="Título">
          <div class="episode-title-cell">
            ${thumb(item.poster_path, item.title)}
            <div class="cell-title">
              <strong>${esc(item.title || '—')}</strong>
              <small>${esc(item.year || '—')}, ${esc(label)}</small>
            </div>
          </div>
        </td>
        <td data-label="Perfil">${badge(item.profile_name || 'Por defecto')}</td>
        <td data-label="Actual">${status(item.current_quality || '—', 'warn')}</td>
        <td data-label="Objetivo">${status(item.cutoff_quality || '—', 'ok')}</td>
        <td data-label="CF Score" class="num">${fmtInt(item.current_cf_score || 0)}</td>
        <td class="actions" data-label="">
          <button class="btn btn-sm btn-ghost" data-action="manual-search" data-id="${item.id}" data-title="${esc(item.title || '')}">${icon('search', 'sm')} Buscar mejora</button>
          <a class="btn btn-sm btn-ghost" href="${href}">Ficha</a>
        </td>
      </tr>`;
    }).join('');

    $('#view').innerHTML = `
      <div class="breadcrumb"><a href="#/catalog">Biblioteca</a>${icon('chevron')}<span>Seguimiento</span></div>
      <div class="tabs section" role="tablist">
        <button class="" data-action="catalog-filter" data-key="tab" data-value="missing" role="tab">Faltantes</button>
        <button class="active" data-action="catalog-filter" data-key="tab" data-value="cutoff" role="tab">Mejorables (Cutoff Unmet) <b>${fmtInt(items.length)}</b></button>
        <button class="" data-action="catalog-filter" data-key="tab" data-value="upcoming" role="tab">Próximos</button>
      </div>
      <div class="toolbar">
        <label class="field grow">
          <span>Buscar</span>
          <input data-input="monitor-q" value="${esc(filters.q)}" placeholder="Filtrar títulos mejorables" autocomplete="off">
        </label>
        <label class="field">
          <span>Tipo</span>
          <select data-change="monitor-kind">
            <option value="" ${filters.kind === '' ? 'selected' : ''}>Todo</option>
            <option value="movie" ${filters.kind === 'movie' ? 'selected' : ''}>Películas</option>
            <option value="series" ${filters.kind === 'series' ? 'selected' : ''}>Series</option>
          </select>
        </label>
        <button class="btn btn-primary" data-action="wanted-upgrade-cutoff">${icon('refresh', 'sm')} Actualizar punteros virtuales</button>
      </div>
      <div class="panel flush section">
        <div class="panel-head">
          <h2>Cutoff Unmet (Virtual)</h2>
          <span class="right tiny faint">Mejora la fuente predeterminada sin descargar torrents hasta reproducir</span>
        </div>
        ${rows ? `<div class="table-wrap"><table class="table--stack">
          <thead><tr><th>Título</th><th>Perfil</th><th>Calidad actual</th><th>Cutoff objetivo</th><th class="num">CF Score</th><th></th></tr></thead>
          <tbody>${rows}</tbody>
        </table></div>` : `<div class="pad">${empty('Todo cumple el Cutoff', 'No hay títulos por debajo de la calidad objetivo de su perfil.')}</div>`}
      </div>
    `;
    return;
  }

  const effectiveState = tab === 'upcoming' ? 'pending' : (filters.state || '');
  const params = new URLSearchParams({
    q: filters.q,
    state: effectiveState,
    kind: filters.kind,
    page: String(filters.page),
    per_page: '50',
  });
  const data = await api(`/api/monitor?${params}`, { signal });
  const items = data.items || [];
  const summary = data.summary || {};
  state.monitorSummary = summary;

  const chips = [
    ['', 'Todos', summary.total || 0],
    ['missing', 'En cola', summary.missing || 0],
    ['pending', 'Sin estrenar', summary.pending || 0],
    ['downloading', 'Descargando', summary.downloading || 0],
  ];

  const rows = items.map((item) => {
    const label = item.kind === 'series'
      ? `T${String(item.season || 0).padStart(2, '0')}E${String(item.episode || 0).padStart(2, '0')}`
      : 'Película';
    const href = item.kind === 'series'
      ? `#/series/${item.tmdb}`
      : `#/title/${item.id}`;
    return `<tr>
      <td data-label="Título">
        <div class="episode-title-cell">
          ${thumb(item.poster_path, item.title)}
          <div class="cell-title">
            <strong>${esc(item.title || '—')}</strong>
            <small>${esc(item.year || '—')}, ${esc(label)}</small>
          </div>
        </div>
      </td>
      <td data-label="Estado">${stateInfo(item)}</td>
      <td data-label="Disponibilidad">${availability(item)}</td>
      <td data-label="Fuentes">${item.releases ? `${fmtInt(item.releases)} <span class="faint">/ ${fmtInt(item.alive)}</span>` : '<span class="faint">—</span>'}</td>
      <td data-label="Última búsqueda">${item.last_search ? esc(fmtRelative(item.last_search)) : '<span class="faint">Nunca</span>'}</td>
      <td class="actions" data-label="">
        <button class="btn btn-sm btn-ghost" data-action="monitor-search" data-key="${esc(item.key)}">${icon('search', 'sm')} Buscar ahora</button>
        <button class="btn btn-sm btn-ghost" data-action="unmonitor-title" data-id="${item.id}">Quitar</button>
        <a class="btn btn-sm btn-ghost" href="${href}">Ficha</a>
      </td>
    </tr>`;
  }).join('');

  $('#view').innerHTML = `
    <div class="breadcrumb"><a href="#/catalog">Biblioteca</a>${icon('chevron')}<span>Seguimiento</span></div>

    <div class="tabs section" role="tablist">
      <button class="${tab === 'missing' ? 'active' : ''}" data-action="catalog-filter" data-key="tab" data-value="missing" role="tab">Faltantes <b>${fmtInt(summary.missing || 0)}</b></button>
      <button class="${tab === 'cutoff' ? 'active' : ''}" data-action="catalog-filter" data-key="tab" data-value="cutoff" role="tab">Mejorables (Cutoff Unmet)</button>
      <button class="${tab === 'upcoming' ? 'active' : ''}" data-action="catalog-filter" data-key="tab" data-value="upcoming" role="tab">Próximos <b>${fmtInt(summary.pending || 0)}</b></button>
    </div>

    <div class="toolbar">
      <label class="field grow">
        <span>Buscar</span>
        <input data-input="monitor-q" value="${esc(filters.q)}" placeholder="Título, serie o clave" autocomplete="off" aria-label="Buscar en seguimiento">
      </label>
      <label class="field">
        <span>Tipo</span>
        <select data-change="monitor-kind" aria-label="Filtrar por tipo">
          <option value="" ${filters.kind === '' ? 'selected' : ''}>Todo</option>
          <option value="movie" ${filters.kind === 'movie' ? 'selected' : ''}>Películas</option>
          <option value="series" ${filters.kind === 'series' ? 'selected' : ''}>Series</option>
        </select>
      </label>
      <button class="btn btn-primary" data-action="monitor-run">${icon('search')} Buscar disponibles</button>
      <button class="btn btn-icon" data-action="monitor-refresh" aria-label="Actualizar" title="Actualizar">${icon('refresh')}</button>
    </div>

    ${tab !== 'upcoming' ? `<div class="panel section" style="padding:12px 14px">
      <div class="row between">
        <div class="chips" role="tablist" aria-label="Estado del seguimiento">
          ${chips.map(([value, label, count]) => `<button class="chip ${filters.state === value ? 'active' : ''}" data-action="monitor-filter" data-value="${value}" role="tab" aria-selected="${filters.state === value}">${esc(label)} <b>${fmtInt(count)}</b></button>`).join('')}
        </div>
        <span class="tiny faint">${fmtInt(summary.total || 0)} en seguimiento</span>
      </div>
    </div>` : ''}

    <div class="panel flush section">
      <div class="panel-head">
        <h2>${tab === 'upcoming' ? 'Próximos estrenos en seguimiento' : 'Cola de seguimiento'}</h2>
        <span class="right tiny faint">RSS cada 30 min, barrido diario 04:20</span>
      </div>
      ${rows ? `<div class="table-wrap"><table class="table--stack">
        <thead><tr><th>Título</th><th>Estado</th><th>Disponibilidad</th><th>Fuentes</th><th>Última búsqueda</th><th></th></tr></thead>
        <tbody>${rows}</tbody>
      </table></div>` : `<div class="pad">${empty(
        filters.q || filters.state || filters.kind ? 'Sin resultados con esos filtros' : 'No hay títulos en seguimiento',
        'Los títulos en seguimiento que aún no tienen acceso aparecen aquí hasta que se encuentra una fuente.',
      )}</div>`}
    </div>

    <div class="row between section">
      <span class="tiny faint">${fmtInt(data.total || 0)} resultados</span>
      <div class="row" style="gap:6px">
        <button class="btn btn-sm btn-ghost" data-action="monitor-page" data-page="${data.page - 1}" ${data.page <= 1 ? 'disabled' : ''}>${icon('arrow-left', 'sm')} Anterior</button>
        <span class="badge mono">${data.page} / ${data.pages}</span>
        <button class="btn btn-sm btn-ghost" data-action="monitor-page" data-page="${data.page + 1}" ${data.page >= data.pages ? 'disabled' : ''}>Siguiente ${icon('arrow-right', 'sm')}</button>
      </div>
    </div>
  `;
}
