import { api } from '../api.js';
import { esc, fmtInt } from '../format.js';
import { updateQuery } from '../router.js';
import { state } from '../store.js';
import { $, badge, empty, icon, poster, setHeader, status } from '../ui.js';

export async function renderAdd({ query, signal }) {
  const kind = query.get('kind') === 'series' ? 'series' : 'movie';
  const text = query.get('q') || '';
  const year = query.get('year') || '';
  const page = Math.max(1, Number(query.get('page')) || 1);
  state.tmdb.kind = kind;
  state.tmdb.query = text;
  state.tmdb.year = year;
  setHeader('Añadir contenido', 'Biblioteca');

  const params = new URLSearchParams({ kind, q: text, year, page: String(page) });
  const data = await api(`/api/tmdb/search?${params}`, { signal });
  const results = data.results || [];
  state.tmdb.results = results;

  const cards = results.map((item) => {
    const isSeries = kind === 'series';
    const href = isSeries ? `#/series/${item.tmdb}` : `#/title/${item.asset_id}`;
    const catalogCount = item.catalog_episodes || 0;
    const libraryCount = item.library_added || item.published_episodes || 0;
    let stateLine = status('No está en el catálogo', 'muted');
    if (item.status === 'downloaded') stateLine = status(isSeries ? `${item.downloaded_episodes || 0}/${catalogCount} descargados` : 'Descargado', 'ok');
    else if (item.status === 'library') stateLine = status(isSeries ? `${libraryCount}/${catalogCount} en biblioteca` : 'En biblioteca', 'ok');
    else if (item.status === 'catalog') stateLine = status('En el catálogo', 'info');
    const action = item.already
      ? `<a class="btn btn-sm btn-ghost" href="${href}">Abrir</a>
         <button class="btn btn-sm btn-ghost" data-action="${isSeries ? 'add-sources' : 'sync-title'}" ${isSeries ? `data-tmdb="${item.tmdb}" data-title="${esc(item.title)}"` : `data-id="${item.asset_id}"`}>${icon('search', 'sm')} Fuentes</button>`
      : `<button class="btn btn-sm btn-primary" data-action="${isSeries ? 'add-series' : 'add-movie'}" data-tmdb="${item.tmdb}" data-title="${esc(item.title)}">${icon('plus', 'sm')} Añadir</button>`;
    const link = item.already
      ? `data-href="${href}" role="link" tabindex="0"`
      : `data-preview="${item.tmdb}" data-kind="${kind}" data-title="${esc(item.title)}" role="link" tabindex="0"`;
    return `<article class="media-card" ${link}>
      <div class="poster">${poster(item.poster_path, item.title)}</div>
      <div class="meta">
        <strong>${esc(item.title)} ${item.year ? `<span class="faint">(${item.year})</span>` : ''}</strong>
        ${stateLine}
        <p class="tiny muted" style="margin:2px 0 0;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden">${esc(item.overview || 'Sin sinopsis en TMDB.')}</p>
        <div class="row" style="margin-top:auto;padding-top:4px">
          <span style="margin-left:auto">${action}</span>
        </div>
      </div>
    </article>`;
  }).join('');

  $('#view').innerHTML = `
    <div class="tabs" role="tablist">
      <button class="${kind === 'movie' ? 'active' : ''}" data-action="add-kind" data-kind="movie" role="tab" aria-selected="${kind === 'movie'}">Películas</button>
      <button class="${kind === 'series' ? 'active' : ''}" data-action="add-kind" data-kind="series" role="tab" aria-selected="${kind === 'series'}">Series</button>
    </div>

    <form class="toolbar" data-form="add-search">
      <label class="field grow">
        <span>Buscar en TMDB</span>
        <input name="q" data-input="add-q" value="${esc(text)}" placeholder="Título original o internacional" autocomplete="off" aria-label="Buscar en TMDB">
      </label>
      <label class="field" style="width:110px">
        <span>Año</span>
        <input name="year" data-input="add-year" value="${esc(year)}" type="number" min="1900" max="2100" placeholder="Opcional" aria-label="Año">
      </label>
      <button class="btn btn-primary" type="submit">${icon('search')} Buscar</button>
    </form>

    <div class="row between section" style="margin-bottom:10px">
      <div class="tiny faint">${text ? `${fmtInt(data.results?.length || 0)} resultados para «${esc(text)}»` : 'Tendencias en TMDB'}</div>
      ${data.total_pages > 1 ? `<div class="row">
        <button class="btn btn-sm btn-ghost" data-action="add-page" data-page="${page - 1}" ${page <= 1 ? 'disabled' : ''}>Anterior</button>
        <span class="tiny faint">Página ${page} de ${fmtInt(data.total_pages)}</span>
        <button class="btn btn-sm btn-ghost" data-action="add-page" data-page="${page + 1}" ${page >= data.total_pages ? 'disabled' : ''}>Siguiente</button>
      </div>` : ''}
    </div>

    ${results.length
      ? `<div class="poster-grid">${cards}</div>`
      : `<div class="panel pad">${empty('Sin resultados', 'Prueba otro título, otro año o el título original.')}</div>`}

    <div class="grid grid-2 section">
      <div class="panel">
        <div class="panel-head"><h2>Alta directa por TMDB</h2></div>
        <div class="panel-body">
          <form class="form-grid" data-form="tmdb-direct">
            <label class="field"><span>Tipo</span><select name="kind">
              <option value="movie" ${kind === 'movie' ? 'selected' : ''}>Película</option>
              <option value="series" ${kind === 'series' ? 'selected' : ''}>Serie</option>
            </select></label>
            <label class="field"><span>ID de TMDB</span><input name="tmdb" type="number" min="1" required></label>
            <label class="field"><span>&nbsp;</span><button class="btn" type="submit">Dar de alta</button></label>
          </form>
        </div>
      </div>
      <div class="panel">
        <div class="panel-head"><h2>Captura por lotes</h2></div>
        <div class="panel-body">
          <a class="btn btn-ghost" href="#/jobs">Abrir tareas</a>
        </div>
      </div>
    </div>
  `;
}
