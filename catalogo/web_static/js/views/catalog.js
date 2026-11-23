import { api } from '../api.js';
import { esc, fmtInt } from '../format.js';
import { originLabel, WATCH, watchState } from '../labels.js';
import { state } from '../store.js';
import { $, badge, empty, icon, poster, setHeader, status, thumb } from '../ui.js';

function primaryStatus(item) {
  if (item.published) {
    if (item.kind === 'series' && item.episodes) {
      return status(`${fmtInt(item.published_episodes || 0)}/${fmtInt(item.episodes)} en Jellyfin`, 'ok');
    }
    return status(WATCH.ready.label, WATCH.ready.tone);
  }
  if (item.download === 'descargando') {
    const progress = Math.round(Number(item.progress || 0) * 100);
    return status(progress ? `Descargando ${progress}%` : 'Descargando', 'info');
  }
  if (item.download === 'pausado') return status('Descarga pausada', 'warn');
  if (item.download === 'error') return status('Error de descarga', 'bad');
  if (item.publication === 'pendiente' || item.downloaded || item.alive) {
    return status(WATCH.pending.label, WATCH.pending.tone);
  }
  const state = watchState(item);
  return status(state.label, state.tone);
}

function card(item, selected) {
  const isSeries = item.kind === 'series';
  const href = isSeries ? `#/series/${item.tmdb}` : `#/title/${item.id}`;
  const key = isSeries ? `series:${item.tmdb}` : item.key;
  const meta = isSeries
    ? `${item.seasons?.length || 0} ${(item.seasons?.length || 0) === 1 ? 'temporada' : 'temporadas'}, ${fmtInt(item.episodes)} episodios`
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
    <div class="poster-progress ${progressClass}" title="${fmtInt(inLibrary)} de ${fmtInt(total)} en Radarr/Sonarr"><i style="width:${Math.round(ratio * 100)}%"></i></div>
    <div class="meta">
      <strong>${esc(item.title || '—')}</strong>
      <small>${esc(item.year || '—')} · ${esc(meta)}</small>
      ${primaryStatus(item)}
    </div>
  </article>`;
}

function row(item, selected) {
  const isSeries = item.kind === 'series';
  const href = isSeries ? `#/series/${item.tmdb}` : `#/title/${item.id}`;
  const key = isSeries ? `series:${item.tmdb}` : item.key;
  const sources = Number(item.alive || 0);
  return `<tr class="${selected ? 'selected' : ''}" data-href="${href}">
    <td data-label="" style="width:28px">
      <input type="checkbox" data-action="catalog-select" data-key="${esc(key)}" data-kind="${esc(item.kind)}" data-tmdb="${item.tmdb || ''}" ${selected ? 'checked' : ''} aria-label="Seleccionar ${esc(item.title || '')}">
    </td>
    <td data-label="Título">
      <a class="title-cell" href="${href}">
        ${thumb(item.poster_path, item.title)}
        <span class="cell-title">
          <strong>${esc(item.title || '—')}</strong>
          <small>${esc(item.year || '—')} · ${isSeries ? `${fmtInt(item.episodes)} episodios` : 'Película'}</small>
        </span>
      </a>
    </td>
    <td data-label="Estado">${primaryStatus(item)}</td>
    <td data-label="Fuentes" class="num">${sources ? `<span class="status info no-dot">${sources}</span>` : '<span class="faint">—</span>'}</td>
    <td data-label="Origen">${item.origin ? badge(originLabel(item.origin), 'mono') : '<span class="faint">—</span>'}</td>
    <td class="actions" data-label="">
      <a class="btn btn-sm btn-ghost" href="${href}">Abrir</a>
    </td>
  </tr>`;
}

export async function renderCatalog({ query, signal }) {
  const filters = {
    q: query.get('q') || '',
    kind: query.get('kind') || '',
    publication: query.get('publication') || '',
    download: query.get('download') || '',
    origin: query.get('origin') || '',
    incomplete: query.get('incomplete') === '1',
    sort: query.get('sort') || 'popularity',
    view: query.get('view') || 'list',
    page: Math.max(1, Number(query.get('page')) || 1),
  };
  state.filters = filters;
  setHeader('Biblioteca');

  const params = new URLSearchParams({
    q: filters.q,
    kind: filters.kind,
    publication: filters.publication,
    download: filters.download,
    origin: filters.origin,
    incomplete: filters.incomplete ? '1' : '',
    sort: filters.sort,
    group: 'series',
    facets: '1',
    page: String(filters.page),
    per_page: '60',
  });
  const data = await api(`/api/ondemand/catalog?${params}`, { signal });
  const facets = data.facets || { publication: {}, download: {}, kind: {}, origin: {} };
  const selected = (key) => state.selection.has(key);
  const cards = data.items.map((item) => card(item, selected(item.kind === 'series' ? `series:${item.tmdb}` : item.key))).join('');
  const rows = data.items.map((item) => row(item, selected(item.kind === 'series' ? `series:${item.tmdb}` : item.key))).join('');
  const totalGroups = data.total || 0;

  const kindChips = [
    ['', 'Todos', (facets.kind?.movie || 0) + (facets.kind?.series || 0)],
    ['movie', 'Películas', facets.kind?.movie || 0],
    ['series', 'Series', facets.kind?.series || 0],
  ];
  const publicationChips = [
    ['', 'Todos', data.total || 0, 'Quita el filtro de Jellyfin'],
    ['publicado', 'En Jellyfin', facets.publication?.publicado || 0, 'Aparece en Jellyfin: TOD ya creó su .strm'],
    ['pendiente', 'Falta en Jellyfin', facets.publication?.pendiente || 0, 'Tiene una fuente, pero todavía no aparece en Jellyfin'],
    ['sin_fuente', 'Sin fuente', facets.publication?.sin_fuente || 0, 'No hay ningún release que reproducir'],
  ];
  const downloadChips = [
    ['', 'Todas', 0, 'Quita el filtro de descargas'],
    ['descargando', 'Descargando', facets.download?.descargando || 0, 'Descarga activa en qBittorrent'],
    ['descargado', 'Descargado', facets.download?.descargado || 0, 'La descarga ya terminó'],
    ['pausado', 'Pausado', facets.download?.pausado || 0, 'Descarga detenida temporalmente'],
    ['error', 'Error', facets.download?.error || 0, 'qBittorrent informó de un error'],
    ['sin_descarga', 'Sin descarga', facets.download?.sin_descarga || 0, 'No hay ninguna descarga asociada'],
  ];

  const bulkPublication = ['pendiente', 'sin_fuente'].includes(filters.publication);
  const bulkCopy = filters.publication === 'sin_fuente'
    ? {
        title: `${fmtInt(data.total || 0)} títulos sin fuente`,
        text: 'TOD buscará un release para cada película y episodio. Puede tardar horas; en el siguiente paso puedes poner un límite.',
        button: `Buscar fuentes en los ${fmtInt(data.total || 0)}`,
      }
    : {
        title: `${fmtInt(data.total || 0)} títulos con fuente, fuera de Jellyfin`,
        text: 'TOD comprobará las fuentes de cada película y episodio y creará su acceso en Jellyfin. Puede tardar; en el siguiente paso puedes poner un límite.',
        button: `Preparar los ${fmtInt(data.total || 0)} para Jellyfin`,
      };

  const originChips = Object.entries(facets.origin || {})
    .filter(([value]) => value && value !== 'mixed')
    .map(([value, count]) => [value, originLabel(value), count]);
  const clearButton = (filters.q || filters.kind || filters.download || filters.publication || filters.origin || filters.incomplete)
    ? `<button class="btn btn-sm btn-ghost" data-action="catalog-clear">${icon('close', 'sm')} Limpiar filtros</button>`
    : '';
  const tmdbButton = filters.q
    ? `<a class="btn btn-sm" href="#/add?q=${encodeURIComponent(filters.q)}">${icon('plus', 'sm')} Añadir «${esc(filters.q)}»</a>`
    : '';

  $('#view').innerHTML = `
    <div class="toolbar">
      <label class="field grow">
        <span>Buscar</span>
        <input data-input="catalog-q" value="${esc(filters.q)}" placeholder="Título, serie o año" autocomplete="off" aria-label="Buscar en la biblioteca">
      </label>
      <label class="field">
        <span>Orden</span>
        <select data-change="catalog-sort" aria-label="Ordenar biblioteca">
          <option value="popularity" ${filters.sort === 'popularity' ? 'selected' : ''}>Popularidad</option>
          <option value="title" ${filters.sort === 'title' ? 'selected' : ''}>Título</option>
          <option value="year" ${filters.sort === 'year' ? 'selected' : ''}>Año</option>
          <option value="recent" ${filters.sort === 'recent' ? 'selected' : ''}>Reciente</option>
        </select>
      </label>
      <div class="view-toggle" role="group" aria-label="Vista de la biblioteca">
        <button class="btn btn-icon ${filters.view === 'list' ? 'active' : ''}" data-action="catalog-view" data-view="list" aria-label="Vista de lista" aria-pressed="${filters.view === 'list'}" title="Lista">${icon('list')}</button>
        <button class="btn btn-icon ${filters.view === 'grid' ? 'active' : ''}" data-action="catalog-view" data-view="grid" aria-label="Vista de carteles" aria-pressed="${filters.view === 'grid'}" title="Carteles">${icon('grid')}</button>
      </div>
      ${clearButton}
    </div>

    <div class="panel section" style="margin-top:10px">
      <div class="panel-head">
        <h2>Filtros</h2>
        <div class="chips" style="margin-left:2px">
          ${kindChips.map(([value, label, count]) => `<button class="chip ${filters.kind === value ? 'active' : ''}" data-action="catalog-filter" data-key="kind" data-value="${value}">${esc(label)} <b>${fmtInt(count)}</b></button>`).join('')}
        </div>
        <div class="right row" style="gap:4px">
          <button class="chip only-mobile" data-action="toggle-filters">${icon('filter', 'sm')} Filtros</button>
          <button class="chip" data-action="catalog-select-all">${icon('check', 'sm')} Seleccionar todo</button>
        </div>
      </div>
      <div class="panel-body filter-details ${state.filtersOpen ? 'open' : ''}">
        <div class="filter-row">
          <span class="filter-label" title="Si aparece en Jellyfin y si hay algo que reproducir">Estado</span>
          <div class="chips">
            ${publicationChips.map(([value, label, count, hint]) => `<button class="chip ${filters.publication === value ? 'active' : ''}" data-action="catalog-filter" data-key="publication" data-value="${value}" title="${esc(hint)}">${esc(label)}${value ? ` <b>${fmtInt(count)}</b>` : ''}</button>`).join('')}
          </div>
        </div>
        <div class="filter-row">
          <span class="filter-label">Descargas</span>
          <div class="chips">
            ${downloadChips.map(([value, label, count, hint]) => `<button class="chip ${filters.download === value ? 'active' : ''}" data-action="catalog-filter" data-key="download" data-value="${value}" title="${esc(hint)}">${esc(label)}${count ? ` <b>${fmtInt(count)}</b>` : ''}</button>`).join('')}
          </div>
        </div>
        ${originChips.length ? `<div class="filter-row">
          <span class="filter-label">Origen</span>
          <div class="chips">
            ${originChips.map(([value, label, count]) => `<button class="chip ${filters.origin === value ? 'active' : ''}" data-action="catalog-filter" data-key="origin" data-value="${esc(value)}">${esc(label)} <b>${fmtInt(count)}</b></button>`).join('')}
          </div>
        </div>` : ''}
      </div>
    </div>

    ${bulkPublication && data.total ? `<div class="bulk-bar">
      <div class="bulk-copy">
        <strong>${esc(bulkCopy.title)}</strong>
        <span>${esc(bulkCopy.text)}</span>
      </div>
      <button class="btn btn-primary" data-action="sync-pending-open" data-publication="${esc(filters.publication)}">
        ${icon('search', 'sm')} ${esc(bulkCopy.button)}
      </button>
    </div>` : ''}

    <div class="row between section" style="margin:14px 0 8px">
      <div class="tiny faint">${fmtInt(totalGroups)} ${totalGroups === 1 ? (filters.kind === 'movie' ? 'película' : filters.kind === 'series' ? 'serie' : 'título') : (filters.kind === 'movie' ? 'películas' : filters.kind === 'series' ? 'series' : 'títulos')}, página ${data.page} de ${data.pages}${filters.kind === 'movie' ? '' : `, ${fmtInt(data.episodes_library_total || 0)} episodios en Radarr/Sonarr`}</div>
      <div class="tiny faint">${filters.view === 'grid' ? 'Carteles' : 'Lista'}, 60 por página</div>
    </div>

    ${data.items.length
      ? filters.view === 'grid'
        ? `<div class="poster-grid">${cards}</div>`
        : `<div class="table-wrap"><table class="table--stack catalog-table">
            <thead><tr><th style="width:28px"></th><th>Título</th><th title="Si aparece en Jellyfin y si hay algo que reproducir">Estado</th><th class="num" title="Releases encontrados para este título">Fuentes</th><th title="De dónde llegó el título al catálogo">Origen</th><th></th></tr></thead>
            <tbody>${rows}</tbody>
          </table></div>`
      : `<div class="panel">${empty('Sin resultados', 'Prueba otro estado, otro tipo o busca el título en TMDB.', `<div class="row">${clearButton}${tmdbButton}</div>`)}</div>`}

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
