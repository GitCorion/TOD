import { api } from '../api.js';
import { esc, fmtInt, fmtShortDate, pct } from '../format.js';
import { updateQuery } from '../router.js';
import { state } from '../store.js';
import { $, badge, bar, empty, icon, poster, setHeader, status } from '../ui.js';

function episodeStatus(ep) {
  if (ep.published) return status('En Jellyfin', 'ok');
  if (ep.download === 'descargando') return status(`Descargando ${pct(ep.progress)}%`, 'info');
  if (ep.download === 'pausado') return status('Pausado', 'warn');
  if (ep.download === 'error') return status('Error', 'bad');
  if (ep.downloaded) return status('Descargado', 'ok');
  if (ep.incomplete) return status('Sin fuente', 'warn');
  if (ep.library_added && ep.monitored && ep.available === false) return status('Sin estrenar', 'muted');
  if (ep.library_added && ep.monitored) return `${status('En Radarr/Sonarr', 'info')} ${status('En seguimiento', 'info')}`;
  if (ep.library_added) return status('En Radarr/Sonarr', 'info');
  if (!ep.in_catalog) return status('Sin catalogar', 'muted');
  return status('Sin fuente', 'muted');
}

function still(path, title) {
  if (path) {
    return `<img class="episode-still" src="https://image.tmdb.org/t/p/w300${esc(path)}" alt="" loading="lazy">`;
  }
  return `<span class="episode-still placeholder">${esc(String(title || '?').slice(0, 1).toUpperCase())}</span>`;
}

function totalRequested(totals) {
  return Boolean(totals.library || totals.monitored);
}

function seasonLabel(season) {
  return season.season === 0 ? 'Especiales' : `Temporada ${season.season}`;
}

export async function renderSeries({ params, query, signal }) {
  const tmdb = Number(params[0] || 0);
  if (!tmdb) { location.hash = '#/catalog'; return; }
  const data = await api(`/api/series/${tmdb}`, { signal });
  state.seriesDetail = data;
  const series = data.series || {};
  const totals = data.totals || {};
  const seasons = data.seasons || [];
  const defaultSeason = seasons.find((entry) => entry.season > 0)?.season ?? seasons[0]?.season ?? 1;
  const currentSeason = Number(query.get('season') || defaultSeason);
  const seasonData = seasons.find((entry) => entry.season === currentSeason) || seasons[0];
  const specialMissing = seasons
    .filter((entry) => entry.season === 0)
    .flatMap((entry) => entry.episodes || [])
    .filter((episode) => !episode.in_catalog).length;
  const allAdded = totals.catalog_episodes > 0
    && (totals.covered || 0) >= totals.catalog_episodes;
  const notAdded = Math.max(0, (totals.episodes || 0) - (totals.catalog_episodes || 0));
  const seriesMode = totals.library || totals.monitored ? 'update' : 'add';
  const seriesAction = totalRequested(totals) ? 'Catalogar episodios que faltan' : 'Catalogar serie';
  const seasonAdded = (seasonData?.catalog_episodes || 0) > 0
    && (seasonData?.covered || 0) >= (seasonData?.catalog_episodes || 0);
  const seasonMode = seasonData?.library || seasonData?.monitored ? 'update' : 'add';
  const seasonAction = seasonData?.library || seasonData?.monitored
    ? 'Catalogar episodios que faltan'
    : 'Catalogar temporada';
  setHeader('Serie', 'Biblioteca');
  document.title = `${series.title || 'Serie'}, TOD`;

  const rows = (seasonData?.episodes || []).map((ep) => {
    const tag = `T${String(ep.season).padStart(2, '0')}E${String(ep.episode).padStart(2, '0')}`;
    const name = ep.name || `Episodio ${ep.episode}`;
    const actions = ep.in_catalog
      ? `${!ep.library_added && !ep.monitored ? `<button class="btn btn-sm btn-ghost" data-action="library-title" data-id="${ep.id}" data-key="${esc(ep.key)}" data-title="${esc(name)}" data-releases="${ep.releases || 0}">Catalogar</button>` : ''}
         ${!ep.published && !ep.monitored ? `<button class="btn btn-sm btn-ghost" data-action="monitor-title" data-id="${ep.id}">Seguir</button>` : ''}
         ${!ep.published && ep.monitored ? `<button class="btn btn-sm btn-ghost" data-action="unmonitor-title" data-id="${ep.id}">Dejar de seguir</button>` : ''}
         ${!ep.downloaded && !ep.published ? `<button class="btn btn-sm" data-action="download-now" data-key="${esc(ep.key)}">Descargar ahora</button>` : ''}
         <a class="btn btn-sm btn-ghost" href="#/title/${ep.id}">Abrir</a>`
      : '<span class="faint">—</span>';
    const sources = ep.releases
      ? `${fmtInt(ep.releases)} <span class="faint">/ ${fmtInt(ep.alive)} vivos</span>`
      : ep.library_added
        ? (ep.incomplete ? status('Sin fuente', 'warn') : status('En Radarr/Sonarr', 'info'))
        : '<span class="faint">—</span>';
    return `<tr class="${ep.in_catalog ? '' : 'row-missing'}">
      <td data-label="Episodio">
        <a class="episode-title-cell" href="#/title/${ep.id}">
          ${still(ep.still_path, name)}
          <div class="cell-title">
            <strong>${esc(name)}</strong>
            <small>${tag}, ${esc(fmtShortDate(ep.air_date))}</small>
          </div>
        </a>
      </td>
      <td data-label="Estado">${episodeStatus(ep)}</td>
      <td data-label="Fuentes">${sources}</td>
      <td data-label="Progreso">${ep.download === 'descargando' || ep.progress ? `<div class="progress-cell">${bar(ep.progress)}<span>${pct(ep.progress)}%</span></div>` : '<span class="faint">—</span>'}</td>
      <td class="actions" data-label="">${actions}</td>
    </tr>`;
  }).join('');

  const seasonChips = seasons.map((entry) => {
    const missing = (entry.episodes || []).filter((episode) => !episode.in_catalog).length;
    const incomplete = (entry.episodes || []).filter((episode) => episode.incomplete).length;
    const note = [
      missing ? `${missing} sin catalogar` : '',
      incomplete ? `${incomplete} sin fuente` : '',
    ].filter(Boolean).join(', ');
    return `<button class="chip ${entry.season === currentSeason ? 'active' : ''}" data-action="series-season" data-season="${entry.season}">
      ${esc(seasonLabel(entry))} <b>${entry.episode_count || entry.episodes.length}</b>${note ? ` <span class="warn-dot" title="${esc(note)}"></span>` : ''}
    </button>`;
  }).join('');

  $('#view').innerHTML = `
    <div class="breadcrumb"><a href="#/catalog">Biblioteca</a>${icon('chevron')}<span>${esc(series.title || 'Serie')}</span></div>

    <div class="panel pad">
      <div class="detail-hero">
        <div class="poster">${poster(series.poster_path, series.title)}</div>
        <div class="info">
          <h2>${esc(series.title || 'Serie')} ${series.year ? `<span class="muted" style="font-size:16px;font-weight:400">(${series.year})</span>` : ''}</h2>
          <div class="row">
            ${badge('serie', 'mono')}
            ${status(`${fmtInt(totals.episodes || 0)} episodios`, 'muted')}
            ${status(`${fmtInt(totals.library || 0)} en Radarr/Sonarr`, 'info')}
            ${totals.monitored ? status(`${fmtInt(totals.monitored)} en seguimiento`, 'info') : ''}
            ${totals.incomplete ? status(`${fmtInt(totals.incomplete)} sin fuente`, 'warn') : ''}
            ${notAdded ? status(`${fmtInt(notAdded)} sin catalogar`, 'muted') : ''}
            ${series.in_library ? badge('en Radarr/Sonarr', 'mono') : ''}
          </div>
          ${series.overview ? `<p class="overview">${esc(series.overview)}</p>` : ''}
          <div class="detail-actions">
            ${allAdded ? '' : `<button class="btn btn-primary" data-action="library-series" data-tmdb="${tmdb}" data-title="${esc(series.title || '')}" data-mode="${seriesMode}">${icon('plus')} ${seriesAction}</button>`}
            <button class="btn" data-action="sync-series" data-tmdb="${tmdb}">${icon('search')} Buscar y añadir a Jellyfin</button>
            ${totals.library ? `<button class="btn btn-danger" data-action="unpublish-series" data-tmdb="${tmdb}">Quitar</button>` : ''}
            <a class="btn btn-ghost" href="${esc(data.tmdb_url || '#')}" target="_blank" rel="noreferrer">${icon('external')} TMDB</a>
          </div>
        </div>
      </div>
    </div>

    ${data.tmdb_error ? `<div class="notice section">No se pudo consultar TMDB. Se muestra solo lo que hay en la base.</div>` : ''}
    ${data.tmdb_errors?.length ? `<div class="notice section">TMDB no devolvió todas las temporadas.</div>` : ''}

    <div class="record-line section">
      <span><b>${fmtInt(totals.episodes || 0)}</b> episodios</span>
      <span><b>${fmtInt(totals.library || 0)}</b> en Radarr/Sonarr</span>
      <span><b>${fmtInt(totals.monitored || 0)}</b> en seguimiento</span>
      <span><b>${fmtInt(totals.incomplete || 0)}</b> sin fuente</span>
      <span><b>${fmtInt(notAdded)}</b> sin catalogar</span>
      ${specialMissing ? `<span><b>${fmtInt(specialMissing)}</b> especiales</span>` : ''}
    </div>

    <div class="panel section">
      <div class="panel-head">
        <div class="chips">${seasonChips}</div>
        <div class="right row" style="gap:6px">
          ${seasonAdded ? '' : `<button class="btn btn-sm btn-ghost" data-action="library-season" data-season="${currentSeason}" data-tmdb="${tmdb}" data-title="${esc(series.title || '')}" data-mode="${seasonMode}">${icon('plus', 'sm')} ${seasonAction}</button>`}
          <button class="btn btn-sm btn-ghost" data-action="sync-season" data-season="${currentSeason}">${icon('search', 'sm')} Buscar y añadir a Jellyfin</button>
          <button class="btn btn-sm btn-ghost" data-action="manual-search-season" data-season="${currentSeason}">${icon('search', 'sm')} Buscar manualmente</button>
        </div>
      </div>
      <div class="panel-body" style="padding:0">
        <div class="row between" style="padding:9px 0;border-bottom:1px solid var(--rule)">
          <span class="tiny muted">${esc(seasonLabel(seasonData || { season: currentSeason }))}</span>
          <span class="row tiny faint" style="gap:14px">
            <span>${fmtInt(seasonData?.episode_count || 0)} episodios</span>
            <span>${fmtInt(seasonData?.library || 0)} en Radarr/Sonarr</span>
            ${seasonData?.incomplete ? `<span class="warn-text">${fmtInt(seasonData.incomplete)} sin fuente</span>` : ''}
          </span>
        </div>
        <div class="table-wrap">
          <table class="table--stack">
            <thead><tr><th>Episodio</th><th>Estado</th><th>Fuentes</th><th>Progreso</th><th></th></tr></thead>
            <tbody>${rows || `<tr><td colspan="5">${empty('Sin episodios', 'TMDB no devolvió episodios para esta temporada.')}</td></tr>`}</tbody>
          </table>
        </div>
      </div>
    </div>
  `;
}
