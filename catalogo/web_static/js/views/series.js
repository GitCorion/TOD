import { api } from '../api.js';
import { esc, fmtInt, fmtShortDate, pct } from '../format.js';
import { updateQuery } from '../router.js';
import { state } from '../store.js';
import { $, badge, bar, empty, icon, poster, setHeader, status } from '../ui.js';

function episodeStatus(ep) {
  if (!ep.in_catalog) return status('Sin catálogo', 'muted');
  if (ep.download === 'descargando') return status(`Descargando ${pct(ep.progress)}%`, 'info');
  if (ep.download === 'pausado') return status('Pausado', 'warn');
  if (ep.download === 'error') return status('Error', 'bad');
  if (ep.downloaded) return status('Descargado', 'ok');
  if (ep.published) return status('En biblioteca', 'ok');
  if (ep.library_added && ep.monitored && ep.available === false) return status('Pendiente de estreno', 'muted');
  if (ep.library_added && ep.monitored) return `${status('En biblioteca, sin enlace', 'warn')} ${status('En seguimiento', 'info')}`;
  if (ep.library_added) return status('En biblioteca, sin enlace', 'warn');
  if (ep.publication === 'pendiente') return ep.alive ? status(`${ep.alive} fuentes`, 'info') : status('Sin verificar', 'warn');
  return status('Sin fuente', 'muted');
}

function still(path, title) {
  if (path) {
    return `<img class="episode-still" src="https://image.tmdb.org/t/p/w300${esc(path)}" alt="" loading="lazy">`;
  }
  return `<span class="episode-still placeholder">${esc(String(title || '?').slice(0, 1).toUpperCase())}</span>`;
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
  const actionableMissing = seasons
    .filter((entry) => entry.season > 0)
    .flatMap((entry) => entry.episodes || [])
    .filter((episode) => !episode.in_catalog).length;
  const specialMissing = seasons
    .filter((entry) => entry.season === 0)
    .flatMap((entry) => entry.episodes || [])
    .filter((episode) => !episode.in_catalog).length;
  setHeader(series.title || 'Serie', 'Serie');

  const rows = (seasonData?.episodes || []).map((ep) => {
    const tag = `T${String(ep.season).padStart(2, '0')}E${String(ep.episode).padStart(2, '0')}`;
    const name = ep.name || `Episodio ${ep.episode}`;
    const actions = ep.in_catalog
      ? `${!ep.library_added ? `<button class="btn btn-sm btn-ghost" data-action="library-title" data-id="${ep.id}" data-key="${esc(ep.key)}" data-title="${esc(name)}" data-releases="${ep.releases || 0}">Añadir a la biblioteca</button>` : ''}
         ${ep.library_added && !ep.published && !ep.monitored ? `<button class="btn btn-sm btn-ghost" data-action="monitor-title" data-id="${ep.id}">Monitorizar</button>` : ''}
         ${ep.library_added && !ep.published && ep.monitored ? `<button class="btn btn-sm btn-ghost" data-action="unmonitor-title" data-id="${ep.id}">Quitar seguimiento</button>` : ''}
         ${!ep.downloaded ? `<button class="btn btn-sm" data-action="download-now" data-key="${esc(ep.key)}">Forzar descarga</button>` : ''}`
      : '<span class="tiny faint">Sin importar</span>';
    return `<tr class="${ep.in_catalog ? '' : 'row-missing'}">
      <td data-label="Episodio">
        <div class="episode-title-cell">
          ${still(ep.still_path, name)}
          <div class="cell-title">
            <strong>${esc(name)}</strong>
            <small>${tag}, ${esc(fmtShortDate(ep.air_date))}</small>
          </div>
        </div>
      </td>
      <td data-label="Estado">${episodeStatus(ep)}</td>
      <td data-label="Fuentes">${ep.releases ? `${fmtInt(ep.releases)} <span class="faint">/ ${fmtInt(ep.alive)} vivos</span>` : '<span class="faint">—</span>'}</td>
      <td data-label="Progreso">${ep.download === 'descargando' || ep.progress ? `<div class="progress-cell">${bar(ep.progress)}<span>${pct(ep.progress)}%</span></div>` : '<span class="faint">—</span>'}</td>
      <td class="actions" data-label="">${actions}</td>
    </tr>`;
  }).join('');

  const seasonChips = seasons.map((entry) => {
    const missing = (entry.episodes || []).filter((episode) => !episode.in_catalog).length;
    return `<button class="chip ${entry.season === currentSeason ? 'active' : ''}" data-action="series-season" data-season="${entry.season}">
      ${esc(seasonLabel(entry))} <b>${entry.episode_count || entry.episodes.length}</b>${missing ? ` <span class="warn-dot" title="${missing} sin catálogo"></span>` : ''}
    </button>`;
  }).join('');

  $('#view').innerHTML = `
    <div class="breadcrumb"><a href="#/catalog">Catálogo</a>${icon('chevron')}<span>${esc(series.title || 'Serie')}</span></div>

    <div class="panel pad">
      <div class="detail-hero">
        <div class="poster">${poster(series.poster_path, series.title)}</div>
        <div class="info">
          <h2>${esc(series.title || 'Serie')} ${series.year ? `<span class="muted" style="font-size:16px;font-weight:400">(${series.year})</span>` : ''}</h2>
          <div class="row">
            ${badge('serie', 'mono')}
            ${status(`${fmtInt(totals.episodes || 0)} episodios`, 'muted')}
            ${status(`${fmtInt(totals.catalog_episodes || 0)} en catálogo`, 'info')}
            ${status(`${fmtInt(totals.library || 0)}/${fmtInt(totals.catalog_episodes || 0)} en biblioteca`, 'ok')}
            ${totals.library > totals.published ? status(`${fmtInt(totals.published || 0)} con enlace`, 'muted') : ''}
            ${actionableMissing ? status(`${fmtInt(actionableMissing)} por importar`, 'warn') : ''}
            ${series.in_library ? badge('en Radarr/Sonarr', 'mono') : ''}
          </div>
          ${series.overview ? `<p class="overview">${esc(series.overview)}</p>` : ''}
          <div class="detail-actions">
            <button class="btn btn-primary" data-action="library-series" data-tmdb="${tmdb}" data-title="${esc(series.title || '')}">${icon('plus')} Añadir toda la serie a la biblioteca</button>
            <button class="btn" data-action="sync-series" data-tmdb="${tmdb}">${icon('search')} Buscar fuentes</button>
            ${totals.library ? `<button class="btn btn-danger" data-action="unpublish-series" data-tmdb="${tmdb}">Quitar de la biblioteca</button>` : ''}
            <a class="btn btn-ghost" href="${esc(data.tmdb_url || '#')}" target="_blank" rel="noreferrer">${icon('external')} TMDB</a>
          </div>
        </div>
      </div>
    </div>

    ${data.tmdb_error ? `<div class="notice section">No se pudo consultar TMDB. Se muestra solo lo que hay en la base.</div>` : ''}
    ${data.tmdb_errors?.length ? `<div class="notice section">TMDB no devolvió todas las temporadas.</div>` : ''}

    <div class="record-line section">
      <span><b>${fmtInt(totals.episodes || 0)}</b> episodios</span>
      <span><b>${fmtInt(totals.catalog_episodes || 0)}</b> en catálogo</span>
      <span><b>${fmtInt(totals.library || 0)}</b> en biblioteca</span>
      <span><b>${fmtInt(totals.published || 0)}</b> con enlace</span>
      <span><b>${fmtInt(actionableMissing)}</b> por importar</span>
      ${specialMissing ? `<span><b>${fmtInt(specialMissing)}</b> especiales</span>` : ''}
    </div>

    <div class="panel section">
      <div class="panel-head">
        <div class="chips">${seasonChips}</div>
        <div class="right row" style="gap:6px">
          <button class="btn btn-sm btn-ghost" data-action="library-season" data-season="${currentSeason}" data-tmdb="${tmdb}" data-title="${esc(series.title || '')}">${icon('plus', 'sm')} Añadir temporada a la biblioteca</button>
          <button class="btn btn-sm btn-ghost" data-action="sync-season" data-season="${currentSeason}">${icon('search', 'sm')} Fuentes</button>
        </div>
      </div>
      <div class="panel-body" style="padding:0">
        <div class="row between" style="padding:9px 0;border-bottom:1px solid var(--rule)">
          <span class="tiny muted">${esc(seasonLabel(seasonData || { season: currentSeason }))}</span>
          <span class="row tiny faint" style="gap:14px">
            <span>${fmtInt(seasonData?.episode_count || 0)} episodios</span>
            <span>${fmtInt(seasonData?.catalog_episodes || 0)} en catálogo</span>
            <span>${fmtInt(seasonData?.library || 0)} en biblioteca</span>
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
