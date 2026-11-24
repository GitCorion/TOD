import { api } from '../api.js';
import { esc, fmtInt, fmtShortDate, pct } from '../format.js';
import { updateQuery } from '../router.js';
import { state } from '../store.js';
import { $, badge, bar, empty, icon, poster, setHeader, status } from '../ui.js';

function episodeStatus(ep) {
  if (ep.available === false) return status('Sin estrenar', 'muted');
  const hasSource = Number(ep.alive || 0) > 0 || Number(ep.releases || 0) > 0;
  const playable = Boolean(ep.downloaded) || Number(ep.alive || 0) > 0;
  if (ep.published) {
    return playable
      ? status('Disponible', 'ok')
      : status('Sin fuente', 'warn');
  }
  if (ep.download === 'descargando') return status(`Descargando ${pct(ep.progress)}%`, 'info');
  if (ep.download === 'pausado') return status('Descarga pausada', 'warn');
  if (ep.download === 'error') return status('Error de descarga', 'bad');
  if (ep.downloaded) return status('Fichero listo, pendiente', 'info');
  if (ep.library_added) {
    if (hasSource) return status('Fuente lista, sin descargar', 'info');
    return status('Sin fuente', 'warn');
  }
  if (ep.monitored) {
    return hasSource
      ? status('Fuente lista, sin descargar', 'info')
      : status('Buscando fuente', 'info');
  }
  if (!ep.in_catalog) return status('Sin catalogar', 'muted');
  return hasSource ? status('Fuente lista', 'info') : status('Sin fuente', 'muted');
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

function seriesState(totals) {
  const aired = Number(totals.aired ?? totals.episodes ?? 0);
  const playable = Number(totals.playable_aired ?? totals.published ?? 0);
  if (aired > 0 && playable >= aired) return status('Disponible', 'ok');
  if (playable > 0) {
    return status(`${fmtInt(playable)}/${fmtInt(aired)} disponibles`, 'info');
  }
  if (Number(totals.downloading || 0) > 0) {
    return status('Descargando', 'info');
  }
  if (Number(totals.published || 0) > 0) {
    return status('Sin fuente', 'warn');
  }
  if (Number(totals.incomplete || 0) > 0 || Number(totals.monitored || 0) > 0) {
    return status('En seguimiento', 'info');
  }
  return status('Sin fuente', 'muted');
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
    .filter((episode) => !episode.in_catalog && episode.available !== false).length;
  const aired = Number(totals.aired ?? totals.episodes ?? 0);
  const allAdded = aired > 0
    && (totals.catalog_aired || 0) >= aired;
  const notAdded = Math.max(0, aired - (totals.catalog_aired || 0));
  const seriesSummary = [
    totals.library ? `${fmtInt(totals.library)} en Radarr/Sonarr` : '',
    totals.monitored_aired ? `${fmtInt(totals.monitored_aired)} en seguimiento` : '',
    totals.incomplete ? `${fmtInt(totals.incomplete)} sin fuente` : '',
    notAdded ? `${fmtInt(notAdded)} sin catalogar` : '',
    totals.upcoming ? `${fmtInt(totals.upcoming)} sin estrenar` : '',
  ].filter(Boolean).join(', ');
  const seriesMode = totals.library || totals.monitored ? 'update' : 'add';
  const seriesAction = totalRequested(totals) ? 'Catalogar episodios que faltan' : 'Catalogar serie';
  const seasonAdded = (seasonData?.aired || 0) > 0
    && (seasonData?.catalog_aired || 0) >= (seasonData?.aired || 0);
  const seasonMode = seasonData?.library || seasonData?.monitored ? 'update' : 'add';
  const seasonAction = seasonData?.library || seasonData?.monitored
    ? 'Catalogar episodios que faltan'
    : 'Catalogar temporada';
  setHeader(series.title || 'Serie');
  document.title = `${series.title || 'Serie'}, TOD`;

  const episodes = seasonData?.episodes || [];
  const showSources = episodes.some((ep) => Number(ep.releases) > 0);
  const showProgress = episodes.some((ep) => ep.download === 'descargando' || ep.progress);
  const columns = 3 + (showSources ? 1 : 0) + (showProgress ? 1 : 0);
  const rows = episodes.map((ep) => {
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
      : '<span class="faint">—</span>';
    return `<tr class="${ep.in_catalog ? '' : 'row-missing'}">
      <td data-label="Episodio">
        <a class="episode-title-cell" href="#/title/${ep.id}">
          ${still(ep.still_path, name)}
          <div class="cell-title">
            <strong>${esc(name)}</strong>
            <small>${tag}, ${esc(fmtShortDate(ep.air_date))}${ep.library_added ? ', En Sonarr' : ''}</small>
          </div>
        </a>
      </td>
      <td data-label="Estado">${episodeStatus(ep)}</td>
      ${showSources ? `<td data-label="Fuentes">${sources}</td>` : ''}
      ${showProgress ? `<td data-label="Progreso">${ep.download === 'descargando' || ep.progress ? `<div class="progress-cell">${bar(ep.progress)}<span>${pct(ep.progress)}%</span></div>` : '<span class="faint">—</span>'}</td>` : ''}
      <td class="actions" data-label="">${actions}</td>
    </tr>`;
  }).join('');

  const seasonChips = seasons.map((entry) => {
    const missing = (entry.episodes || [])
      .filter((episode) => !episode.in_catalog && episode.available !== false).length;
    const incomplete = (entry.episodes || []).filter((episode) => episode.incomplete).length;
    const upcoming = (entry.episodes || []).filter((episode) => episode.available === false).length;
    const note = [
      missing ? `${missing} sin catalogar` : '',
      incomplete ? `${incomplete} sin fuente` : '',
      upcoming ? `${upcoming} sin estrenar` : '',
    ].filter(Boolean).join(', ');
    const warn = missing || incomplete;
    return `<button class="chip ${entry.season === currentSeason ? 'active' : ''}" data-action="series-season" data-season="${entry.season}">
      ${esc(seasonLabel(entry))} <b>${entry.episode_count || entry.episodes.length}</b>${warn ? ` <span class="warn-dot" title="${esc(note)}"></span>` : ''}
    </button>`;
  }).join('');

  $('#view').innerHTML = `
    <div class="breadcrumb"><a href="#/catalog">Biblioteca</a>${icon('chevron')}<span>${esc(series.title || 'Serie')}</span></div>

    <div class="panel pad detail-card">
      <div class="detail-hero">
        <div class="poster">${poster(series.poster_path, series.title)}</div>
        <div class="info">
          <h2>${esc(series.title || 'Serie')} ${series.year ? `<span class="muted" style="font-size:16px;font-weight:400">(${series.year})</span>` : ''}</h2>
          <div class="row">
            ${badge('serie', 'mono')}
            ${badge(`${fmtInt(totals.episodes || 0)} episodios`, 'mono')}
            ${seriesState(totals)}
          </div>
          ${seriesSummary ? `<p class="tiny muted" style="margin:6px 0 0">${esc(seriesSummary)}</p>` : ''}
          ${series.overview ? `<p class="overview">${esc(series.overview)}</p>` : ''}
          <div class="detail-actions">
            ${allAdded ? '' : `<button class="btn btn-primary" data-action="library-series" data-tmdb="${tmdb}" data-title="${esc(series.title || '')}" data-mode="${seriesMode}">${icon('plus')} ${seriesAction}</button>`}
            <button class="btn" data-action="sync-series" data-tmdb="${tmdb}">${icon('search')} Buscar y añadir a Jellyfin</button>
            <a class="btn btn-ghost" href="${esc(data.tmdb_url || '#')}" target="_blank" rel="noreferrer">${icon('external')} TMDB</a>
            ${totals.library ? `<button class="btn btn-sm btn-ghost danger-action" data-action="unpublish-series" data-tmdb="${tmdb}">Quitar de TOD</button>` : ''}
          </div>
        </div>
      </div>
    </div>

    ${data.tmdb_error ? `<div class="notice section">No se pudo consultar TMDB. Se muestra solo lo que hay en la base.</div>` : ''}
    ${data.tmdb_errors?.length ? `<div class="notice section">TMDB no devolvió todas las temporadas.</div>` : ''}

    <div class="record-line section">
      <span><b>${fmtInt(totals.episodes || 0)}</b> episodios</span>
      <span><b>${fmtInt(totals.library || 0)}</b> en Radarr/Sonarr</span>
      <span><b>${fmtInt(totals.monitored_aired || 0)}</b> en seguimiento</span>
      <span><b>${fmtInt(totals.incomplete || 0)}</b> sin fuente</span>
      <span><b>${fmtInt(notAdded)}</b> sin catalogar</span>
      ${totals.upcoming ? `<span><b>${fmtInt(totals.upcoming)}</b> sin estrenar</span>` : ''}
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
        <div class="row between" style="padding:8px 12px;border-bottom:1px solid var(--rule)">
          <span class="tiny muted">${esc(seasonLabel(seasonData || { season: currentSeason }))}</span>
          <span class="row tiny faint" style="gap:14px">
            <span>${fmtInt(seasonData?.episode_count || 0)} episodios</span>
            <span>${fmtInt(seasonData?.library || 0)} en Radarr/Sonarr</span>
            ${seasonData?.incomplete ? `<span class="warn-text">${fmtInt(seasonData.incomplete)} sin fuente</span>` : ''}
            ${seasonData?.upcoming ? `<span>${fmtInt(seasonData.upcoming)} sin estrenar</span>` : ''}
          </span>
        </div>
        <div class="table-wrap">
          <table class="table--stack">
            <thead><tr><th>Episodio</th><th>Estado</th>${showSources ? '<th>Fuentes</th>' : ''}${showProgress ? '<th>Progreso</th>' : ''}<th></th></tr></thead>
            <tbody>${rows || `<tr><td colspan="${columns}">${empty('Sin episodios', 'TMDB no devolvió episodios para esta temporada.')}</td></tr>`}</tbody>
          </table>
        </div>
      </div>
    </div>
  `;
}
