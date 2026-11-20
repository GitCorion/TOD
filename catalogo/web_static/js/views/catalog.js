import { api } from '../api.js';
import { esc, fmtInt, pct, posterUrl } from '../format.js';
import { downloadStatus, publicationStatus } from '../labels.js';
import { navigate, updateQuery } from '../router.js';
import { state } from '../store.js';
import { $, badge, empty, icon, poster, setHeader, skeletonGrid, status } from '../ui.js';

function primaryStatus(item) {
  if (item.kind === 'series') {
    const counts = item.download_counts || {};
    if (counts.descargando) return status('Descargando', 'info');
    if (counts.pausado) return status('Pausado', 'warn');
    if (counts.error) return status('Error', 'bad');
    if (counts.descargado) return status(`${counts.descargado}/${item.episodes} descargados`, 'ok');
    if (item.library_episodes) return status(`${fmtInt(item.library_episodes)}/${fmtInt(item.episodes)} en biblioteca`, 'ok');
    if (item.publication === 'pendiente') {
      return item.alive ? status(`${item.alive} fuentes`, 'info') : status('Sin verificar', 'warn');
    }
    return status('Sin fuente', 'muted');
  }
  if (item.download === 'descargando') return status('Descargando', 'info');
  if (item.download === 'pausado') return status('Pausado', 'warn');
  if (item.download === 'error') return status('Error', 'bad');
  if (item.downloaded) return status('Descargado', 'ok');
  if (item.library_added) return status('En biblioteca', 'ok');
  if (item.publication === 'pendiente') {
    return item.alive ? status(`${item.alive} fuentes`, 'info') : status('Sin verificar', 'warn');
  }
  return status('Sin fuente', 'muted');
}

function card(item, selected) {
  const isSeries = item.kind === 'series';
  const href = isSeries ? `#/series/${item.tmdb}` : `#/title/${item.id}`;
  const key = isSeries ? `series:${item.tmdb}` : item.key;
  const meta = isSeries
    ? `${item.seasons?.length || 0} ${(item.seasons?.length || 0) === 1 ? 'temporada' : 'temporadas'}, ${fmtInt(item.episodes)} en catálogo`
    : 'Película';
  const total = isSeries ? Number(item.episodes || 0) : 1;
  const inLibrary = isSeries
    ? Number(item.library_episodes || 0)
    : (item.library_added ? 1 : 0);
  const ratio = total ? inLibrary / total : 0;
  const progressClass = ratio >= 1 ? 'done' : ratio > 0 ? 'partial' : 'none';
  return `<article class="media-card ${selected ? 'selected' : ''}" data-href="${href}" tabindex="0" role="link" aria-label="${esc(item.title || '')}">
    <div class="poster">
      <input type="checkbox" class="check" data-action="catalog-select" data-key="${esc(key)}" data-kind="${esc(item.kind)}" data-tmdb="${item.tmdb || ''}" ${selected ? 'checked' : ''} aria-label="Seleccionar ${esc(item.title || '')}">
      ${poster(item.poster_path, item.title)}
    </div>
    <div class="poster-progress ${progressClass}" title="${fmtInt(inLibrary)} de ${fmtInt(total)} en biblioteca"><i style="width:${Math.round(ratio * 100)}%"></i></div>
    <div class="meta">
      <strong>${esc(item.title || '—')}</strong>
      <small>${esc(item.year || '—')}</small>
      <small>${esc(meta)}</small>
      ${primaryStatus(item)}
    </div>
  </article>`;
}

function row(item, selected) {
  const isSeries = item.kind === 'series';
  const href = isSeries ? `#/series/${item.tmdb}` : `#/title/${item.id}`;
  const key = isSeries ? `series:${item.tmdb}` : item.key;
  const meta = isSeries
    ? `${item.year || '—'}, ${item.seasons?.length || 0} ${(item.seasons?.length || 0) === 1 ? 'temporada' : 'temporadas'}, ${fmtInt(item.episodes)} en catálogo`
    : `${item.year || '—'}, Película`;
  return `<div class="media-row ${selected ? 'selected' : ''}" data-href="${href}" tabindex="0" role="link">
    <input type="checkbox" data-action="catalog-select" data-key="${esc(key)}" data-kind="${esc(item.kind)}" data-tmdb="${item.tmdb || ''}" ${selected ? 'checked' : ''} aria-label="Seleccionar ${esc(item.title || '')}">
    <span class="poster">${poster(item.poster_path, item.title, { size: 'w92' })}</span>
    <div class="info">
      <strong>${esc(item.title || '—')}</strong>
      <small>${esc(meta)}</small>
    </div>
    <div class="side">
      ${primaryStatus(item)}
      <a class="btn btn-sm btn-ghost" href="${href}">Abrir</a>
    </div>
  </div>`;
}

export async function renderCatalog({ query, signal }) {
  const filters = {
    q: query.get('q') || '',
    kind: query.get('kind') || '',
    publication: query.get('publication') || 'publicado',
    download: query.get('download') || '',
    sort: query.get('sort') || 'popularity',
    view: query.get('view') || 'grid',
    page: Math.max(1, Number(query.get('page')) || 1),
  };
  state.filters = filters;
  setHeader('Catálogo', 'Biblioteca');

  const params = new URLSearchParams({
    q: filters.q,
    kind: filters.kind,
    publication: filters.publication,
    download: filters.download,
    sort: filters.sort,
    group: 'series',
    facets: '1',
    page: String(filters.page),
    per_page: '60',
  });
  const data = await api(`/api/ondemand/catalog?${params}`, { signal });
  const facets = data.facets || { publication: {}, download: {}, kind: {} };
  const selected = (key) => state.selection.has(key);
  const cards = data.items.map((item) => card(item, selected(item.kind === 'series' ? `series:${item.tmdb}` : item.key))).join('');
  const rows = data.items.map((item) => row(item, selected(item.kind === 'series' ? `series:${item.tmdb}` : item.key))).join('');
  const totalGroups = data.total || 0;

  const pubTabs = [
    ['publicado', 'En biblioteca', facets.publication?.publicado || 0],
    ['pendiente', 'Por añadir', facets.publication?.pendiente || 0],
    ['sin_fuente', 'Sin fuente', facets.publication?.sin_fuente || 0],
  ];
  const kindChips = [
    ['', 'Todo', (facets.kind?.movie || 0) + (facets.kind?.series || 0)],
    ['movie', 'Películas', facets.kind?.movie || 0],
    ['series', 'Series', facets.kind?.series || 0],
  ];
  const downloadChips = [
    ['', 'Cualquier estado', 0],
    ['descargando', 'Descargando', facets.download?.descargando || 0],
    ['descargado', 'Descargado', facets.download?.descargado || 0],
    ['pausado', 'Pausado', facets.download?.pausado || 0],
    ['error', 'Error', facets.download?.error || 0],
    ['sin_descarga', 'Sin descarga', facets.download?.sin_descarga || 0],
  ];

  const clearButton = (filters.q || filters.kind || filters.download)
    ? `<button class="btn btn-sm btn-ghost" data-action="catalog-clear">${icon('close', 'sm')} Limpiar filtros</button>`
    : '';

  const pendingAction = ['pendiente', 'sin_fuente'].includes(filters.publication)
    ? `<div class="row section">
        <button class="btn btn-primary" data-action="sync-pending-open" data-publication="${filters.publication}">
          ${icon('search')} Buscar fuentes en todas
        </button>
      </div>`
    : '';

  $('#view').innerHTML = `
    <div class="toolbar">
      <label class="field grow">
        <span>Buscar</span>
        <input data-input="catalog-q" value="${esc(filters.q)}" placeholder="Título, serie o año" autocomplete="off" aria-label="Buscar en el catálogo">
      </label>
      <label class="field">
        <span>Orden</span>
        <select data-change="catalog-sort" aria-label="Ordenar catálogo">
          <option value="popularity" ${filters.sort === 'popularity' ? 'selected' : ''}>Popularidad</option>
          <option value="title" ${filters.sort === 'title' ? 'selected' : ''}>Título</option>
          <option value="year" ${filters.sort === 'year' ? 'selected' : ''}>Año</option>
          <option value="recent" ${filters.sort === 'recent' ? 'selected' : ''}>Reciente</option>
        </select>
      </label>
      <div class="row" style="gap:4px">
        <button class="btn btn-icon ${filters.view === 'grid' ? 'active' : ''}" data-action="catalog-view" data-view="grid" aria-label="Vista de carteles" aria-pressed="${filters.view === 'grid'}" title="Carteles">${icon('grid')}</button>
        <button class="btn btn-icon ${filters.view === 'list' ? 'active' : ''}" data-action="catalog-view" data-view="list" aria-label="Vista de lista" aria-pressed="${filters.view === 'list'}" title="Lista">${icon('list')}</button>
      </div>
      ${clearButton}
    </div>

    <div class="panel section" style="padding:12px 14px">
      <div class="row between">
        <div class="chips" role="tablist" aria-label="Estado de publicación">
          ${pubTabs.map(([value, label, count]) => `<button class="chip ${filters.publication === value ? 'active' : ''}" data-action="catalog-filter" data-key="publication" data-value="${value}" role="tab" aria-selected="${filters.publication === value}">${esc(label)} <b>${fmtInt(count)}</b></button>`).join('')}
        </div>
        <button class="chip only-mobile ${(filters.kind || filters.download) ? 'active' : ''}" data-action="toggle-filters">
          ${icon('filter', 'sm')} Filtros${(filters.kind || filters.download) ? `, ${(filters.kind ? 1 : 0) + (filters.download ? 1 : 0)}` : ''}
        </button>
      </div>
      <div class="filter-details ${state.filtersOpen ? 'open' : ''}">
        <div class="chips">
          ${kindChips.map(([value, label, count]) => `<button class="chip ${filters.kind === value ? 'active' : ''}" data-action="catalog-filter" data-key="kind" data-value="${value}">${esc(label)} <b>${fmtInt(count)}</b></button>`).join('')}
        </div>
        <div class="chips">
          ${downloadChips.map(([value, label, count]) => `<button class="chip ${filters.download === value ? 'active' : ''}" data-action="catalog-filter" data-key="download" data-value="${value}">${esc(label)}${count ? ` <b>${fmtInt(count)}</b>` : ''}</button>`).join('')}
        </div>
      </div>
    </div>

    <div class="row between section" style="margin-bottom:10px">
      <div class="tiny faint">${fmtInt(totalGroups)} ${totalGroups === 1 ? (filters.kind === 'movie' ? 'película' : filters.kind === 'series' ? 'serie' : 'título') : (filters.kind === 'movie' ? 'películas' : filters.kind === 'series' ? 'series' : 'títulos')}, página ${data.page} de ${data.pages}${filters.kind === 'movie' ? '' : `, ${fmtInt(data.episodes_library_total || 0)} episodios en biblioteca`}</div>
      <div class="tiny faint">${filters.view === 'grid' ? 'Carteles' : 'Lista'}, 60 por página</div>
    </div>

    ${pendingAction}

    ${data.items.length
      ? filters.view === 'grid'
        ? `<div class="poster-grid">${cards}</div>`
        : `<div class="panel flush"><div class="media-rows">${rows}</div></div>`
      : `<div class="panel pad">${empty('Sin resultados', 'Prueba otro estado, otro tipo o limpia los filtros.', clearButton)}</div>`}

    <div class="row between section">
      <span class="tiny faint">${fmtInt(totalGroups)} resultados</span>
      <div class="row" style="gap:6px">
        <button class="btn btn-sm btn-ghost" data-action="catalog-page" data-page="${data.page - 1}" ${data.page <= 1 ? 'disabled' : ''}>${icon('arrow-left', 'sm')} Anterior</button>
        <span class="badge mono">${data.page} / ${data.pages}</span>
        <button class="btn btn-sm btn-ghost" data-action="catalog-page" data-page="${data.page + 1}" ${data.page >= data.pages ? 'disabled' : ''}>Siguiente ${icon('arrow-right', 'sm')}</button>
      </div>
    </div>
  `;
}
