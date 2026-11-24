import { api } from '../api.js';
import { esc, fmtInt, fmtShortDate } from '../format.js';
import { originLabel, WATCH, watchState } from '../labels.js';
import { state } from '../store.js';
import { $, empty, icon, poster, setHeader, status, thumb } from '../ui.js';

function isPlayable(item) {
  return Number(item.alive || 0) > 0 || Boolean(item.has_file) || Boolean(item.downloaded);
}

function stateInfo(item) {
  const playable = isPlayable(item);
  if (item.published) {
    if (playable) {
      if (item.kind === 'series' && item.episodes) {
        const aired = Number(item.aired_episodes || item.episodes || 0);
        const published = Number(item.published_aired || item.published_episodes || 0);
        if (!aired) {
          return {
            label: 'Sin estrenar',
            tone: 'muted',
            hint: 'Todavía no se ha emitido ningún episodio',
          };
        }
        if (published >= aired) {
          return {
            label: WATCH.ready.label,
            tone: WATCH.ready.tone,
            hint: item.upcoming_episodes
              ? `${fmtInt(item.upcoming_episodes)} episodios sin estrenar`
              : 'Todos los episodios emitidos están disponibles',
          };
        }
        return {
          label: `${fmtInt(published)}/${fmtInt(aired)} disponibles`,
          tone: 'info',
          hint: `${fmtInt(aired)} episodios emitidos${item.upcoming_episodes ? `, ${fmtInt(item.upcoming_episodes)} sin estrenar` : ''}`,
        };
      }
      return { label: WATCH.ready.label, tone: WATCH.ready.tone };
    }
    return {
      label: 'Sin fuente',
      tone: 'warn',
      hint: 'El .strm existe, pero todavía no hay fuente viva ni fichero',
    };
  }
  if (item.download === 'descargando') {
    const progress = Math.round(Number(item.progress || 0) * 100);
    return {
      label: progress ? `Descargando ${progress}%` : 'Descargando',
      tone: 'info',
    };
  }
  if (item.download === 'pausado') return { label: 'Descarga pausada', tone: 'warn' };
  if (item.download === 'error') return { label: 'Error de descarga', tone: 'bad' };
  if (item.publication === 'pendiente' || item.downloaded || item.alive) {
    return { label: WATCH.pending.label, tone: WATCH.pending.tone };
  }
  const watch = watchState(item);
  return { label: watch.label, tone: watch.tone };
}

function coverage(item) {
  if (item.kind !== 'series') return item.published ? 100 : 0;
  const total = Number(item.aired_episodes || item.episodes || 0);
  return total
    ? Math.round((Number(item.published_aired || item.published_episodes || 0) / total) * 100)
    : 0;
}

function card(item, selected, priority) {
  const isSeries = item.kind === 'series';
  const href = isSeries ? `#/series/${item.tmdb}` : `#/title/${item.id}`;
  const key = isSeries ? `series:${item.tmdb}` : item.key;
  const info = stateInfo(item);
  const sources = Number(item.alive || 0);
  const meta = isSeries
    ? `${fmtInt(item.episodes)} ${Number(item.episodes) === 1 ? 'episodio' : 'episodios'}`
    : 'Película';
  const sourceLabel = `${fmtInt(sources)} ${sources === 1 ? 'fuente' : 'fuentes'}`;
  const rail = coverage(item);
  return `<article class="catalog-card ${selected ? 'selected' : ''}">
    <a class="catalog-link" href="${href}" aria-label="${esc(item.title || '')}, ${esc(item.year || '')}, ${esc(info.label)}, ${sources ? `${fmtInt(sources)} fuentes vivas` : 'sin fuentes vivas'}">
      <div class="catalog-cover">
        <div class="poster">${poster(item.poster_path, item.title, { priority })}</div>
        <span class="catalog-state ${info.tone}" title="${esc(info.hint || info.label)}"><i aria-hidden="true"></i>${esc(info.label)}</span>
        <span class="catalog-sources ${sources ? '' : 'is-zero'}" title="${sources ? `${sourceLabel} vivas` : 'Sin fuentes vivas'}">${sourceLabel}</span>
        ${isSeries ? `<span class="catalog-rail ${info.tone}" aria-hidden="true"><i style="width:${rail}%"></i></span>` : ''}
      </div>
      <div class="catalog-meta">
        <strong title="${esc(item.title || '')}">${esc(item.title || '—')}</strong>
        <small>${esc(item.year || '—')}, ${esc(meta)}</small>
      </div>
    </a>
    <label class="catalog-pick">
      <input type="checkbox" class="catalog-check" data-action="catalog-select" data-key="${esc(key)}" data-kind="${esc(item.kind)}" data-tmdb="${item.tmdb || ''}" ${selected ? 'checked' : ''} aria-label="Seleccionar ${esc(item.title || '')}">
    </label>
  </article>`;
}

function row(item, selected) {
  const isSeries = item.kind === 'series';
  const href = isSeries ? `#/series/${item.tmdb}` : `#/title/${item.id}`;
  const key = isSeries ? `series:${item.tmdb}` : item.key;
  const info = stateInfo(item);
  const sources = Number(item.alive || 0);
  return `<tr class="${selected ? 'selected' : ''}" data-href="${href}">
    <td class="pick">
      <input type="checkbox" data-action="catalog-select" data-key="${esc(key)}" data-kind="${esc(item.kind)}" data-tmdb="${item.tmdb || ''}" ${selected ? 'checked' : ''} aria-label="Seleccionar ${esc(item.title || '')}">
    </td>
    <td data-label="Título">
      <a class="title-cell" href="${href}">
        ${thumb(item.poster_path, item.title)}
        <span class="cell-title">
          <strong>${esc(item.title || '—')}</strong>
          <small>${esc(item.year || '—')}, ${isSeries ? `${fmtInt(item.episodes)} episodios` : 'Película'}</small>
        </span>
      </a>
    </td>
    <td data-label="Estado">${status(info.label, info.tone)}</td>
    <td data-label="Fuentes" class="num"><span class="mono ${sources ? '' : 'faint'}">${fmtInt(sources)}</span></td>
    <td data-label="Procedencia">${item.origin ? `<span class="badge">${esc(originLabel(item.origin))}</span>` : '<span class="faint">—</span>'}</td>
    <td data-label="Añadido" class="nowrap"><span class="tiny faint">${fmtShortDate(item.recent_at)}</span></td>
    <td class="actions" data-label="">
      <a class="btn btn-sm btn-ghost" href="${href}">Abrir</a>
    </td>
  </tr>`;
}

function filterChip({ label, count, active, action, key, value, hint }) {
  const data = key
    ? `data-action="${action}" data-key="${key}" data-value="${esc(value)}"`
    : `data-action="${action}"`;
  return `<button class="seg-item ${active ? 'active' : ''}" ${data}${hint ? ` title="${esc(hint)}"` : ''}>${esc(label)}${count !== null ? ` <b>${fmtInt(count)}</b>` : ''}</button>`;
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
    view: query.get('view') || 'grid',
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
  const cards = data.items.map((item, index) => card(item, selected(item.kind === 'series' ? `series:${item.tmdb}` : item.key), index < 8)).join('');
  const rows = data.items.map((item) => row(item, selected(item.kind === 'series' ? `series:${item.tmdb}` : item.key))).join('');
  const totalGroups = data.total || 0;
  const navLibrary = document.getElementById('navLibrary');
  if (!filters.q) state.libraryTotal = (facets.kind?.movie || 0) + (facets.kind?.series || 0);
  if (navLibrary) navLibrary.textContent = state.libraryTotal ? fmtInt(state.libraryTotal) : '';

  const kindTotal = (facets.kind?.movie || 0) + (facets.kind?.series || 0);
  const publicationTotal = (facets.publication?.publicado || 0)
    + (facets.publication?.pendiente || 0)
    + (facets.publication?.sin_fuente || 0);
  const kindChips = [
    { label: 'Todos', value: '', count: null },
    { label: 'Películas', value: 'movie', count: facets.kind?.movie || 0 },
    { label: 'Series', value: 'series', count: facets.kind?.series || 0 },
  ];
  const publicationChips = [
    { label: 'Todos', value: '', count: publicationTotal || kindTotal, hint: 'Quita el filtro de disponibilidad' },
    { label: 'Disponible', value: 'publicado', count: facets.publication?.publicado || 0, hint: 'Está en Jellyfin: TOD ya creó su .strm' },
    { label: 'Pendiente', value: 'pendiente', count: facets.publication?.pendiente || 0, hint: 'Tiene una fuente, pero todavía no está en Jellyfin' },
    { label: 'Sin fuente', value: 'sin_fuente', count: facets.publication?.sin_fuente || 0, hint: 'No hay ningún release que reproducir' },
  ];
  const downloadChips = [
    { label: 'Todas', value: '', count: null, hint: 'Quita el filtro de descargas' },
    { label: 'Descargando', value: 'descargando', count: facets.download?.descargando || 0, hint: 'Descarga activa en qBittorrent' },
    { label: 'Descargado', value: 'descargado', count: facets.download?.descargado || 0, hint: 'La descarga ya terminó' },
    { label: 'Pausado', value: 'pausado', count: facets.download?.pausado || 0, hint: 'Descarga detenida temporalmente' },
    { label: 'Error', value: 'error', count: facets.download?.error || 0, hint: 'qBittorrent informó de un error' },
    { label: 'Sin descarga', value: 'sin_descarga', count: facets.download?.sin_descarga || 0, hint: 'No hay ninguna descarga asociada' },
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
    .map(([value, count]) => ({ label: originLabel(value), value, count }));

  const advancedCount = [filters.download, filters.origin, filters.incomplete ? '1' : '']
    .filter(Boolean).length;
  const activeAdvanced = [
    filters.download ? {
      label: (downloadChips.find((chip) => chip.value === filters.download) || {}).label || filters.download,
      key: 'download',
      value: '',
    } : null,
    filters.origin ? {
      label: originLabel(filters.origin),
      key: 'origin',
      value: '',
    } : null,
    filters.incomplete ? { label: 'Solo incompletos', action: 'catalog-incomplete' } : null,
  ].filter(Boolean);
  const clearButton = (filters.q || filters.kind || filters.download || filters.publication || filters.origin || filters.incomplete)
    ? `<button class="btn btn-sm btn-ghost" data-action="catalog-clear">${icon('close', 'sm')} Limpiar filtros</button>`
    : '';
  const tmdbButton = filters.q
    ? `<a class="btn btn-sm" href="#/add?q=${encodeURIComponent(filters.q)}">${icon('plus', 'sm')} Añadir «${esc(filters.q)}»</a>`
    : '';
  const hasFilters = Boolean(filters.q || filters.kind || filters.publication || filters.download || filters.origin || filters.incomplete);
  const sourceSummary = filters.kind === 'movie' || !totalGroups || hasFilters
    ? ''
    : `, ${fmtInt(data.episodes_library_total || 0)} episodios en Radarr/Sonarr`;
  const noun = filters.kind === 'movie'
    ? (totalGroups === 1 ? 'película' : 'películas')
    : filters.kind === 'series'
      ? 'series'
      : (totalGroups === 1 ? 'título' : 'títulos');
  const resultLabel = !totalGroups
    ? 'Sin resultados'
    : hasFilters
      ? `${fmtInt(totalGroups)} de ${fmtInt(filters.kind ? kindTotal : publicationTotal)} ${noun}`
      : `${fmtInt(totalGroups)} ${noun}`;

  $('#view').innerHTML = `
    <div class="library">
      <div class="library-head">
      <div class="library-bar">
        <label class="library-search">
          ${icon('search')}
          <input data-input="catalog-q" value="${esc(filters.q)}" placeholder="Buscar por título, serie o año" autocomplete="off" aria-label="Buscar en la biblioteca">
        </label>
        <div class="seg-group">
          <span class="seg-label">Tipo</span>
          <div class="seg" role="group" aria-label="Tipo de contenido">
            ${kindChips.map((chip) => filterChip({
              label: chip.label,
              count: chip.count,
              active: filters.kind === chip.value,
              action: 'catalog-filter',
              key: 'kind',
              value: chip.value,
            })).join('')}
          </div>
        </div>
        <div class="view-toggle" role="group" aria-label="Vista de la biblioteca">
          <button class="btn btn-icon ${filters.view === 'grid' ? 'active' : ''}" data-action="catalog-view" data-view="grid" aria-label="Vista de carteles" aria-pressed="${filters.view === 'grid'}" title="Carteles">${icon('grid')}</button>
          <button class="btn btn-icon ${filters.view === 'list' ? 'active' : ''}" data-action="catalog-view" data-view="list" aria-label="Vista de lista" aria-pressed="${filters.view === 'list'}" title="Lista">${icon('list')}</button>
        </div>
      </div>

      <div class="library-subbar">
        <div class="seg-group">
          <span class="seg-label">Estado</span>
          <div class="seg seg-quiet" role="group" aria-label="Disponibilidad">
            ${publicationChips.map((chip) => filterChip({
              label: chip.label,
              count: chip.count,
              active: filters.publication === chip.value,
              action: 'catalog-filter',
              key: 'publication',
              value: chip.value,
              hint: chip.hint,
            })).join('')}
          </div>
        </div>
        ${activeAdvanced.map((chip) => `<button class="chip chip-clear" ${chip.action ? `data-action="${chip.action}"` : `data-action="catalog-filter" data-key="${chip.key}" data-value=""`}>${esc(chip.label)} ${icon('close', 'sm')}</button>`).join('')}
        <label class="library-sort">
          <span class="sr-only">Orden</span>
          <select data-change="catalog-sort" aria-label="Ordenar biblioteca">
            <option value="popularity" ${filters.sort === 'popularity' ? 'selected' : ''}>Popularidad</option>
            <option value="title" ${filters.sort === 'title' ? 'selected' : ''}>Título</option>
            <option value="year" ${filters.sort === 'year' ? 'selected' : ''}>Año</option>
            <option value="recent" ${filters.sort === 'recent' ? 'selected' : ''}>Añadido</option>
          </select>
        </label>
        <span class="library-total">${resultLabel}<span class="library-episodes">${sourceSummary}</span></span>
        <details class="library-more">
          <summary class="btn btn-sm btn-ghost">${icon('filter', 'sm')} Más filtros${advancedCount ? ` (${advancedCount})` : ''}</summary>
          <div class="library-more-body">
            <div class="filter-line">
              <span class="filter-name">Descargas</span>
              <div class="seg seg-quiet">
                ${downloadChips.map((chip) => filterChip({
                  label: chip.label,
                  count: chip.count,
                  active: filters.download === chip.value,
                  action: 'catalog-filter',
                  key: 'download',
                  value: chip.value,
                  hint: chip.hint,
                })).join('')}
              </div>
            </div>
            ${originChips.length ? `<div class="filter-line">
              <span class="filter-name">Procedencia</span>
              <div class="seg seg-quiet">
                ${originChips.map((chip) => filterChip({
                  label: chip.label,
                  count: chip.count,
                  active: filters.origin === chip.value,
                  action: 'catalog-filter',
                  key: 'origin',
                  value: chip.value,
                })).join('')}
              </div>
            </div>` : ''}
            <div class="filter-line">
              <span class="filter-name">Seguimiento</span>
              <div class="seg seg-quiet">
                <button class="seg-item ${filters.incomplete ? 'active' : ''}" data-action="catalog-incomplete">Solo incompletos <b>${fmtInt(facets.incomplete || 0)}</b></button>
              </div>
            </div>
            <div class="filter-foot">
              ${clearButton}
              <button class="btn btn-sm btn-ghost" data-action="catalog-select-all">${icon('check', 'sm')} Seleccionar todo</button>
            </div>
          </div>
        </details>
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

      ${data.items.length
        ? filters.view === 'grid'
          ? `<div class="poster-grid">${cards}</div>`
          : `<div class="table-wrap"><table class="table--stack catalog-table">
              <thead><tr><th class="pick"></th><th>Título</th><th>Estado</th><th class="num">Fuentes</th><th>Procedencia</th><th>Añadido</th><th></th></tr></thead>
              <tbody>${rows}</tbody>
            </table></div>`
        : `<div class="panel">${empty('Sin resultados', 'Prueba otro estado, otro tipo o busca el título en TMDB.', `<div class="row">${clearButton}${tmdbButton}</div>`)}</div>`}

      <div class="library-foot">
        <span class="tiny faint">Página ${data.page} de ${data.pages}. ${fmtInt(totalGroups)} ${totalGroups === 1 ? 'resultado' : 'resultados'}, 60 por página.</span>
        <div class="row" style="gap:6px">
          <button class="btn btn-sm btn-ghost" data-action="catalog-page" data-page="${data.page - 1}" ${data.page <= 1 ? 'disabled' : ''}>${icon('arrow-left', 'sm')} Anterior</button>
          <span class="badge mono">${data.page} / ${data.pages}</span>
          <button class="btn btn-sm btn-ghost" data-action="catalog-page" data-page="${data.page + 1}" ${data.page >= data.pages ? 'disabled' : ''}>Siguiente ${icon('arrow-right', 'sm')}</button>
        </div>
      </div>
    </div>
  `;
}
