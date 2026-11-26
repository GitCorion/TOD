import { api } from '../api.js';
import { esc, fmtBytes, fmtInt, fmtShortDate } from '../format.js';
import { originLabel, WATCH, watchState } from '../labels.js';
import { cachedApi, state } from '../store.js';
import { $, badge, barPercent, empty, icon, poster, setHeader, status, thumb } from '../ui.js';

const ALL_COLS = [
  { id: 'estado', label: 'Estado', def: true },
  { id: 'perfil', label: 'Perfil', def: true },
  { id: 'calidad', label: 'Calidad', def: true },
  { id: 'progreso', label: 'Progreso', def: true },
  { id: 'proxima', label: 'Próx. emisión', def: false },
  { id: 'tags', label: 'Etiquetas', def: true },
  { id: 'root', label: 'Carpeta', def: false },
  { id: 'fuentes', label: 'Fuentes', def: true },
  { id: 'peso', label: 'Peso local', def: false },
  { id: 'procedencia', label: 'Procedencia', def: false },
  { id: 'anadido', label: 'Añadido', def: true },
];

function getVisibleCols() {
  try {
    const raw = localStorage.getItem('tod.catalogCols');
    if (raw) {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed) && parsed.length) return new Set(parsed);
    }
  } catch (_) {}
  return new Set(ALL_COLS.filter((c) => c.def).map((c) => c.id));
}

function isPlayable(item) {
  return Number(item.alive || 0) > 0 || Boolean(item.has_file) || Boolean(item.downloaded);
}

function stateInfo(item) {
  const playable = isPlayable(item);
  if (!playable && item.download === 'descargando') {
    const progress = Math.round(Number(item.progress || 0) * 100);
    return { label: progress ? `Descargando ${progress}%` : 'Descargando', tone: 'info' };
  }
  if (!playable && item.download === 'pausado') return { label: 'Descarga pausada', tone: 'warn' };
  if (!playable && item.download === 'error') return { label: 'Error de descarga', tone: 'bad' };
  if (item.published) {
    if (playable) {
      if (item.kind === 'series' && item.episodes) {
        const aired = Number(item.aired_episodes || item.episodes || 0);
        const published = Number(item.published_aired || item.published_episodes || 0);
        if (!aired) return { label: 'Sin estrenar', tone: 'muted', hint: 'Todavía no se ha emitido ningún episodio' };
        if (published >= aired) {
          return {
            label: WATCH.ready.label,
            tone: WATCH.ready.tone,
            hint: item.upcoming_episodes ? `${fmtInt(item.upcoming_episodes)} sin estrenar` : 'Todos emitidos disponibles',
          };
        }
        return { label: `${fmtInt(published)}/${fmtInt(aired)} disp.`, tone: 'info' };
      }
      return { label: WATCH.ready.label, tone: WATCH.ready.tone };
    }
    return { label: 'Sin fuente', tone: 'warn', hint: 'El .strm existe, sin fuente viva' };
  }
  if (item.downloaded) {
    return { label: 'Descargado', tone: 'info', hint: 'Fichero descargado listo para reproducir' };
  }
  const watch = watchState(item);
  return { label: watch.label, tone: watch.tone };
}

function coverage(item) {
  if (item.kind !== 'series') return item.published ? 100 : 0;
  const total = Number(item.aired_episodes || item.episodes || 0);
  return total ? Math.round((Number(item.published_aired || item.published_episodes || 0) / total) * 100) : 0;
}

function card(item, selected, priority) {
  const isSeries = item.kind === 'series';
  const href = isSeries ? `#/series/${item.tmdb}` : `#/title/${item.id}`;
  const key = isSeries ? `series:${item.tmdb}` : item.key;
  const info = stateInfo(item);
  const sources = Number(item.alive || 0);
  const meta = isSeries
    ? `${fmtInt(item.regular_episodes ?? item.episodes)} ${Number(item.regular_episodes ?? item.episodes) === 1 ? 'episodio' : 'episodios'}`
    : 'Película';
  const qBadge = sources && item.best_quality && item.best_quality !== 'unknown'
    ? ` · ${item.best_quality}`
    : '';
  const sourceLabel = `${fmtInt(sources)} ${sources === 1 ? 'fuente' : 'fuentes'}`;
  const rail = coverage(item);
  return `<article class="catalog-card ${selected ? 'selected' : ''}">
    <a class="catalog-link" href="${href}" aria-label="${esc(item.title || '')}">
      <div class="catalog-cover">
        <div class="poster">${poster(item.poster_path, item.title, { priority })}</div>
        <span class="catalog-state ${info.tone}" title="${esc(info.hint || info.label)}"><i aria-hidden="true"></i>${esc(info.label)}</span>
        <span class="catalog-sources ${sources ? '' : 'is-zero'}">${sourceLabel}${esc(qBadge)}</span>
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

function row(item, selected, cols) {
  const isSeries = item.kind === 'series';
  const href = isSeries ? `#/series/${item.tmdb}` : `#/title/${item.id}`;
  const key = isSeries ? `series:${item.tmdb}` : item.key;
  const info = stateInfo(item);
  const sources = Number(item.alive || 0);
  const prog = coverage(item);
  const epRatio = isSeries
    ? `${fmtInt(item.published_aired || item.published_episodes || 0)}/${fmtInt(item.aired_episodes || item.episodes || 0)}`
    : (item.published ? '1/1' : '0/1');
  const qText = [
    item.best_quality && item.best_quality !== 'unknown' ? item.best_quality : '',
    item.best_audio && item.best_audio !== 'unknown' ? item.best_audio : '',
  ].filter(Boolean).join(' · ');
  const tagsHtml = (item.tags || []).map((t) => `<span class="tag-pill tiny" style="--tag-color:${esc(t.color || '#6b7280')}"><i aria-hidden="true"></i>${esc(t.name)}</span>`).join(' ');

  return `<tr class="${selected ? 'selected' : ''}" data-href="${href}">
    <td class="pick">
      <input type="checkbox" data-action="catalog-select" data-key="${esc(key)}" data-kind="${esc(item.kind)}" data-tmdb="${item.tmdb || ''}" ${selected ? 'checked' : ''} aria-label="Seleccionar ${esc(item.title || '')}">
    </td>
    <td data-label="Título">
      <a class="title-cell" href="${href}">
        ${thumb(item.poster_path, item.title)}
        <span class="cell-title">
          <strong>${esc(item.title || '—')}</strong>
          <small>${esc(item.year || '—')}, ${isSeries ? `${fmtInt(item.episodes)} ep.` : 'Película'}${item.series_status ? ` · ${esc(item.series_status)}` : ''}</small>
        </span>
      </a>
    </td>
    ${cols.has('estado') ? `<td data-label="Estado">${status(info.label, info.tone)}${item.cutoff_unmet ? ' <span class="status warn" title="Por debajo del perfil objetivo">Mejorable</span>' : ''}</td>` : ''}
    ${cols.has('perfil') ? `<td data-label="Perfil" class="tiny">${esc(item.profile_name || '—')}</td>` : ''}
    ${cols.has('calidad') ? `<td data-label="Calidad" class="tiny">${qText ? badge(qText, 'mono') : '<span class="faint">—</span>'}</td>` : ''}
    ${cols.has('progreso') ? `<td data-label="Progreso"><div class="progress-cell">${barPercent(prog, prog >= 100 ? 'green' : '')}<span class="tiny mono">${epRatio}</span></div></td>` : ''}
    ${cols.has('proxima') ? `<td data-label="Próx. emisión" class="tiny faint nowrap">${item.next_air_date ? fmtShortDate(item.next_air_date) : '—'}</td>` : ''}
    ${cols.has('tags') ? `<td data-label="Etiquetas">${tagsHtml || '<span class="faint">—</span>'}</td>` : ''}
    ${cols.has('root') ? `<td data-label="Carpeta" class="tiny faint">${esc(item.root_folder_name || 'Por defecto')}</td>` : ''}
    ${cols.has('fuentes') ? `<td data-label="Fuentes" class="num"><span class="mono ${sources ? '' : 'faint'}">${fmtInt(sources)}</span></td>` : ''}
    ${cols.has('peso') ? `<td data-label="Peso" class="num tiny faint">${item.disk_bytes ? fmtBytes(item.disk_bytes) : '—'}</td>` : ''}
    ${cols.has('procedencia') ? `<td data-label="Procedencia">${item.origin ? `<span class="badge">${esc(originLabel(item.origin))}</span>` : '<span class="faint">—</span>'}</td>` : ''}
    ${cols.has('anadido') ? `<td data-label="Añadido" class="nowrap"><span class="tiny faint">${fmtShortDate(item.recent_at)}</span></td>` : ''}
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
    profile_id: query.get('profile_id') || '',
    quality: query.get('quality') || '',
    audio: query.get('audio') || '',
    privacy: query.get('privacy') || '',
    tracker: query.get('tracker') || '',
    tag: query.get('tag') || '',
    genre: query.get('genre') || '',
    root_folder: query.get('root_folder') || '',
    monitored: query.get('monitored') || '',
    cutoff_unmet: query.get('cutoff_unmet') === '1',
    sort: query.get('sort') || (() => { try { return localStorage.getItem('tod.catalogSort') || 'popularity'; } catch (_) { return 'popularity'; } })(),
    view: (() => {
      const qv = query.get('view');
      const v = qv || (() => { try { return localStorage.getItem('tod.catalogView') || 'grid'; } catch (_) { return 'grid'; } })();
      return ['table', 'list'].includes(v) ? 'table' : 'grid';
    })(),
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
    profile_id: filters.profile_id,
    quality: filters.quality,
    audio: filters.audio,
    privacy: filters.privacy,
    tracker: filters.tracker,
    tag: filters.tag,
    genre: filters.genre,
    root_folder: filters.root_folder,
    monitored: filters.monitored,
    cutoff_unmet: filters.cutoff_unmet ? '1' : '',
    sort: filters.sort,
    group: 'series',
    facets: '1',
    page: String(filters.page),
    per_page: '60',
  });
  const [data, cfData, profData, rfData, tagData] = await Promise.all([
    api(`/api/ondemand/catalog?${params}`, { signal }),
    cachedApi('custom-filters', () => api('/api/custom-filters?target=catalog', { signal }).catch(() => ({ items: [] }))),
    cachedApi('profiles', () => api('/api/profiles', { signal }).catch(() => ({ items: [] }))),
    cachedApi('root-folders', () => api('/api/root-folders', { signal }).catch(() => ({ items: [] }))),
    cachedApi('tags', () => api('/api/tags', { signal }).catch(() => ({ items: [] }))),
  ]);

  const facets = data.facets || { publication: {}, download: {}, kind: {}, origin: {} };
  const cols = getVisibleCols();
  const selected = (key) => state.selection.has(key);
  const cards = data.items.map((item, index) => card(item, selected(item.kind === 'series' ? `series:${item.tmdb}` : item.key), index < 8)).join('');
  const rows = data.items.map((item) => row(item, selected(item.kind === 'series' ? `series:${item.tmdb}` : item.key), cols)).join('');
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
    { label: 'Todos', value: '', count: publicationTotal || kindTotal, hint: 'Muestra todo' },
    { label: 'Disponibles', value: 'publicado', count: facets.publication?.publicado || 0, hint: 'Listo para reproducir en Jellyfin' },
    { label: 'Sin fuente', value: 'sin_fuente', count: facets.publication?.sin_fuente || 0, hint: 'Sin fuente viva compatible actualmente' },
  ];
  const downloadChips = [
    { label: 'Todas', value: '', count: null },
    { label: 'Descargando', value: 'descargando', count: facets.download?.descargando || 0 },
    { label: 'Sembrando', value: 'descargado', count: facets.download?.descargado || 0 },
    { label: 'Pausado', value: 'pausado', count: facets.download?.pausado || 0 },
    { label: 'Error', value: 'error', count: facets.download?.error || 0 },
    { label: 'Sin descarga', value: 'sin_descarga', count: facets.download?.sin_descarga || 0 },
  ];

  const bulkPublication = filters.publication === 'sin_fuente';
  const bulkCopy = filters.publication === 'sin_fuente'
    ? {
        title: `${fmtInt(data.total || 0)} títulos sin fuente viva`,
        text: 'TOD buscará releases en Prowlarr y Jackett para cada película y episodio.',
        button: `Buscar releases (${fmtInt(data.total || 0)})`,
      }
    : {
        title: `${fmtInt(data.total || 0)} títulos listos para añadir a Jellyfin`,
        text: 'Tienen fuente válida o fichero local pero aún no tienen acceso .strm en Jellyfin.',
        button: `Generar .strm para los ${fmtInt(data.total || 0)}`,
      };

  const originChips = Object.entries(facets.origin || {})
    .filter(([value]) => value && value !== 'mixed')
    .map(([value, count]) => ({ label: originLabel(value), value, count }));

  const advKeys = ['download', 'origin', 'profile_id', 'quality', 'audio', 'privacy', 'tracker', 'tag', 'genre', 'root_folder', 'monitored'];
  const advancedCount = advKeys.filter((k) => Boolean(filters[k])).length + (filters.incomplete ? 1 : 0) + (filters.cutoff_unmet ? 1 : 0);

  const activeChips = [
    filters.download ? { label: (downloadChips.find((c) => c.value === filters.download) || {}).label || filters.download, key: 'download' } : null,
    filters.origin ? { label: originLabel(filters.origin), key: 'origin' } : null,
    filters.profile_id ? { label: `Perfil: ${(profData.items || []).find((p) => String(p.id) === String(filters.profile_id))?.name || filters.profile_id}`, key: 'profile_id' } : null,
    filters.quality ? { label: `Calidad: ${filters.quality}`, key: 'quality' } : null,
    filters.audio ? { label: `Audio: ${filters.audio}`, key: 'audio' } : null,
    filters.privacy ? { label: filters.privacy === 'private' ? 'Solo privados' : 'Solo públicos', key: 'privacy' } : null,
    filters.tracker ? { label: `Tracker: ${filters.tracker}`, key: 'tracker' } : null,
    filters.tag ? { label: `Etiqueta: ${filters.tag}`, key: 'tag' } : null,
    filters.genre ? { label: `Género: ${filters.genre}`, key: 'genre' } : null,
    filters.root_folder ? { label: `Carpeta: ${(rfData.items || []).find((r) => String(r.id) === String(filters.root_folder))?.name || filters.root_folder}`, key: 'root_folder' } : null,
    filters.monitored === '1' ? { label: 'Monitorizados', key: 'monitored' } : null,
    filters.monitored === '0' ? { label: 'Sin monitorizar', key: 'monitored' } : null,
    filters.incomplete ? { label: 'Solo incompletos', action: 'catalog-incomplete' } : null,
    filters.cutoff_unmet ? { label: 'Mejorables', key: 'cutoff_unmet' } : null,
  ].filter(Boolean);

  const hasAnyFilter = Boolean(filters.q || filters.kind || filters.publication || advancedCount);
  const clearButton = hasAnyFilter
    ? `<button class="btn btn-sm btn-ghost" data-action="catalog-clear">${icon('close', 'sm')} Limpiar filtros</button>`
    : '';
  const tmdbButton = filters.q
    ? `<a class="btn btn-sm" href="#/add?q=${encodeURIComponent(filters.q)}">${icon('plus', 'sm')} Añadir «${esc(filters.q)}»</a>`
    : '';
  const noun = filters.kind === 'movie'
    ? (totalGroups === 1 ? 'película' : 'películas')
    : filters.kind === 'series' ? 'series' : (totalGroups === 1 ? 'título' : 'títulos');
  const resultLabel = !totalGroups
    ? 'Sin resultados'
    : hasAnyFilter
      ? `${fmtInt(totalGroups)} de ${fmtInt(filters.kind ? kindTotal : publicationTotal)} ${noun}`
      : `${fmtInt(totalGroups)} ${noun}`;
  const customFilters = cfData.items || [];

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
            <button class="btn btn-icon ${filters.view === 'table' ? 'active' : ''}" data-action="catalog-view" data-view="table" aria-label="Vista de tabla" aria-pressed="${filters.view === 'table'}" title="Tabla">${icon('list')}</button>
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
          ${activeChips.map((chip) => `<button class="chip chip-clear" ${chip.action ? `data-action="${chip.action}"` : `data-action="catalog-filter" data-key="${chip.key}" data-value=""`}>${esc(chip.label)} ${icon('close', 'sm')}</button>`).join('')}
          <label class="library-sort">
            <span class="sr-only">Orden</span>
            <select data-change="catalog-sort" aria-label="Ordenar biblioteca">
              <option value="popularity" ${filters.sort === 'popularity' ? 'selected' : ''}>Popularidad</option>
              <option value="title" ${filters.sort === 'title' ? 'selected' : ''}>Título</option>
              <option value="year" ${filters.sort === 'year' ? 'selected' : ''}>Año</option>
              <option value="recent" ${filters.sort === 'recent' ? 'selected' : ''}>Añadido</option>
            </select>
          </label>
          <span class="library-total">${resultLabel}</span>
          <button class="btn btn-sm btn-ghost" data-action="catalog-select-all" title="Seleccionar o deseleccionar todos los títulos de esta página">${icon('check', 'sm')} Seleccionar todo</button>
          ${filters.view === 'table' ? `<details class="library-more">
            <summary class="btn btn-sm btn-ghost">Columnas (${cols.size})</summary>
            <div class="library-more-body" style="width:220px">
              <div class="stack" style="gap:6px">
                ${ALL_COLS.map((c) => `<label class="inline check tiny">
                  <input type="checkbox" data-change="catalog-col" value="${c.id}" ${cols.has(c.id) ? 'checked' : ''}>
                  ${esc(c.label)}
                </label>`).join('')}
              </div>
            </div>
          </details>` : ''}
          <details class="library-more">
            <summary class="btn btn-sm btn-ghost">${icon('filter', 'sm')} Más filtros${advancedCount ? ` (${advancedCount})` : ''}</summary>
            <div class="library-more-body" style="width:min(540px, 92vw)">
              <div class="form-grid" style="margin-bottom:10px">
                <label class="field"><span>Perfil</span>
                  <select class="input input-sm" data-change="catalog-adv" data-filter-key="profile_id">
                    <option value="">Todos</option>
                    ${(profData.items || []).map((p) => `<option value="${p.id}" ${String(filters.profile_id) === String(p.id) ? 'selected' : ''}>${esc(p.name)}</option>`).join('')}
                  </select>
                </label>
                <label class="field"><span>Calidad</span>
                  <select class="input input-sm" data-change="catalog-adv" data-filter-key="quality">
                    <option value="">Todas</option>
                    ${['2160p', '1080p', '720p', '480p'].map((q) => `<option value="${q}" ${filters.quality === q ? 'selected' : ''}>${q}</option>`).join('')}
                  </select>
                </label>
                <label class="field"><span>Audio</span>
                  <select class="input input-sm" data-change="catalog-adv" data-filter-key="audio">
                    <option value="">Todos</option>
                    ${['castellano', 'vose', 'latino', 'unknown'].map((a) => `<option value="${a}" ${filters.audio === a ? 'selected' : ''}>${a}</option>`).join('')}
                  </select>
                </label>
                <label class="field"><span>Privacidad</span>
                  <select class="input input-sm" data-change="catalog-adv" data-filter-key="privacy">
                    <option value="">Todas</option>
                    <option value="private" ${filters.privacy === 'private' ? 'selected' : ''}>Solo privados</option>
                    <option value="public" ${filters.privacy === 'public' ? 'selected' : ''}>Solo públicos</option>
                  </select>
                </label>
                <label class="field"><span>Tracker</span>
                  <select class="input input-sm" data-change="catalog-adv" data-filter-key="tracker">
                    <option value="">Todos</option>
                    ${Object.keys(facets.trackers || {}).map((tr) => `<option value="${esc(tr)}" ${filters.tracker === tr ? 'selected' : ''}>${esc(tr)} (${facets.trackers[tr]})</option>`).join('')}
                  </select>
                </label>
                <label class="field"><span>Etiqueta</span>
                  <select class="input input-sm" data-change="catalog-adv" data-filter-key="tag">
                    <option value="">Todas</option>
                    ${(tagData.items || []).map((t) => `<option value="${esc(t.name)}" ${filters.tag === t.name ? 'selected' : ''}>${esc(t.name)}</option>`).join('')}
                  </select>
                </label>
                <label class="field"><span>Género</span>
                  <select class="input input-sm" data-change="catalog-adv" data-filter-key="genre">
                    <option value="">Todos</option>
                    ${Object.keys(facets.genres || {}).map((g) => `<option value="${esc(g)}" ${filters.genre === g ? 'selected' : ''}>${esc(g)}</option>`).join('')}
                  </select>
                </label>
                <label class="field"><span>Carpeta</span>
                  <select class="input input-sm" data-change="catalog-adv" data-filter-key="root_folder">
                    <option value="">Todos</option>
                    ${(rfData.items || []).map((rf) => `<option value="${rf.id}" ${String(filters.root_folder) === String(rf.id) ? 'selected' : ''}>${esc(rf.name)}</option>`).join('')}
                  </select>
                </label>
              </div>
              <div class="filter-line">
                <span class="filter-name">Descargas</span>
                <div class="seg seg-quiet">
                  ${downloadChips.map((chip) => filterChip({ label: chip.label, count: chip.count, active: filters.download === chip.value, action: 'catalog-filter', key: 'download', value: chip.value })).join('')}
                </div>
              </div>
              ${originChips.length ? `<div class="filter-line">
                <span class="filter-name">Procedencia</span>
                <div class="seg seg-quiet">
                  ${originChips.map((chip) => filterChip({ label: chip.label, count: chip.count, active: filters.origin === chip.value, action: 'catalog-filter', key: 'origin', value: chip.value })).join('')}
                </div>
              </div>` : ''}
              <div class="filter-line">
                <span class="filter-name">Monitorización</span>
                <div class="seg seg-quiet">
                  <button class="seg-item ${filters.incomplete ? 'active' : ''}" data-action="catalog-incomplete">Incompletos <b>${fmtInt(facets.incomplete || 0)}</b></button>
                  <button class="seg-item ${filters.cutoff_unmet ? 'active' : ''}" data-action="catalog-filter" data-key="cutoff_unmet" data-value="${filters.cutoff_unmet ? '' : '1'}">Mejorables <b>${fmtInt(facets.cutoff_unmet || 0)}</b></button>
                  <button class="seg-item ${filters.monitored === '1' ? 'active' : ''}" data-action="catalog-filter" data-key="monitored" data-value="${filters.monitored === '1' ? '' : '1'}">Monitorizados</button>
                </div>
              </div>
              <div class="filter-foot">
                ${clearButton}
                <div class="row" style="gap:6px;margin-left:auto">
                  ${hasAnyFilter ? `<button class="btn btn-sm btn-ghost" data-action="custom-filter-save">${icon('plus', 'sm')} Guardar filtro</button>` : ''}
                  <button class="btn btn-sm btn-ghost" data-action="catalog-select-all">${icon('check', 'sm')} Seleccionar todo</button>
                </div>
              </div>
            </div>
          </details>
        </div>

        ${customFilters.length ? `<div class="row" style="gap:6px;flex-wrap:wrap;margin-top:8px">
          <span class="tiny faint">Filtros guardados:</span>
          ${customFilters.map((cf) => `<span class="chip">
            <button class="chip-btn" data-action="custom-filter-apply" data-filters="${esc(JSON.stringify(cf.filters))}">${esc(cf.name)}</button>
            <button class="chip-del" data-action="custom-filter-delete" data-id="${cf.id}" title="Borrar filtro">${icon('close', 'sm')}</button>
          </span>`).join('')}
        </div>` : ''}
      </div>

      ${bulkPublication && data.total ? `<div class="bulk-bar">
        <div class="bulk-copy"><strong>${esc(bulkCopy.title)}</strong><span>${esc(bulkCopy.text)}</span></div>
        <button class="btn btn-primary" data-action="sync-pending-open" data-publication="${esc(filters.publication)}">${icon('search', 'sm')} ${esc(bulkCopy.button)}</button>
      </div>` : ''}

      ${data.items.length
        ? filters.view === 'grid'
          ? `<div class="poster-grid">${cards}</div>`
          : `<div class="table-wrap" style="margin-top:14px"><table class="table--stack catalog-table">
              <thead><tr>
                <th class="pick"><input type="checkbox" data-action="catalog-select-all" aria-label="Seleccionar todos" title="Seleccionar todos"></th>
                <th>Título</th>
                ${cols.has('estado') ? '<th>Estado</th>' : ''}
                ${cols.has('perfil') ? '<th>Perfil</th>' : ''}
                ${cols.has('calidad') ? '<th>Calidad</th>' : ''}
                ${cols.has('progreso') ? '<th>Progreso</th>' : ''}
                ${cols.has('proxima') ? '<th>Próx. emisión</th>' : ''}
                ${cols.has('tags') ? '<th>Etiquetas</th>' : ''}
                ${cols.has('root') ? '<th>Carpeta</th>' : ''}
                ${cols.has('fuentes') ? '<th class="num">Fuentes</th>' : ''}
                ${cols.has('peso') ? '<th class="num">Peso</th>' : ''}
                ${cols.has('procedencia') ? '<th>Procedencia</th>' : ''}
                ${cols.has('anadido') ? '<th>Añadido</th>' : ''}
                <th></th>
              </tr></thead>
              <tbody>${rows}</tbody>
            </table></div>`
        : `<div class="panel" style="margin-top:16px">${empty('Sin resultados', 'Prueba otro filtro o busca el título en TMDB.', `<div class="row">${clearButton}${tmdbButton}</div>`)}</div>`}

      <div class="library-foot">
        <span class="tiny faint">Página ${data.page} de ${data.pages}. ${fmtInt(totalGroups)} resultados, 60 por página.</span>
        <div class="row" style="gap:6px">
          <button class="btn btn-sm btn-ghost" data-action="catalog-page" data-page="${data.page - 1}" ${data.page <= 1 ? 'disabled' : ''}>${icon('arrow-left', 'sm')} Anterior</button>
          <span class="badge mono">${data.page} / ${data.pages}</span>
          <button class="btn btn-sm btn-ghost" data-action="catalog-page" data-page="${data.page + 1}" ${data.page >= data.pages ? 'disabled' : ''}>Siguiente ${icon('arrow-right', 'sm')}</button>
        </div>
      </div>
    </div>
  `;
}
