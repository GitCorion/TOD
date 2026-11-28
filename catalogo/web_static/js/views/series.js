import { api } from '../api.js';
import { esc, fmtInt, fmtShortDate, pct } from '../format.js';
import { updateQuery } from '../router.js';
import { state } from '../store.js';
import { $, backdropUrl, badge, bar, empty, icon, poster, setActiveNav, setHeader, status } from '../ui.js';

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
  if (!ep.in_catalog) return status('Sin añadir', 'muted');
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
  if (Number(totals.catalog_aired || 0) > 0) {
    return status('Sin fuente', 'warn');
  }
  if (Number(totals.incomplete || 0) > 0 || Number(totals.monitored || 0) > 0) {
    return status('Monitorizada', 'info');
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
  const aired = Number(totals.aired ?? totals.episodes ?? 0);
  const allAdded = aired > 0
    && (totals.catalog_aired || 0) >= aired;
  const notAdded = Math.max(0, aired - (totals.catalog_aired || 0));
  const seriesSummary = [
    totals.library ? `${fmtInt(totals.library)} en biblioteca` : '',
    totals.monitored_aired ? `${fmtInt(totals.monitored_aired)} monitorizados` : '',
    totals.incomplete ? `${fmtInt(totals.incomplete)} sin fuente` : '',
    notAdded ? `${fmtInt(notAdded)} sin añadir` : '',
    totals.upcoming ? `${fmtInt(totals.upcoming)} sin estrenar` : '',
  ].filter(Boolean).join(', ');
  const seriesMode = totals.library || totals.monitored ? 'update' : 'add';
  const seriesAction = totalRequested(totals) ? 'Añadir episodios faltantes' : 'Añadir serie';
  const seriesTrackable = Math.max(0, Number(totals.catalog_episodes || 0) - Number(totals.library || 0));
  const seriesFollowed = Math.min(seriesTrackable, Number(totals.monitored || 0));
  const seasonEpisodes = seasonData?.episodes || [];
  const seasonTrackable = seasonEpisodes.filter((ep) => ep.in_catalog && !ep.library_added).length;
  const seasonFollowed = seasonEpisodes.filter((ep) => ep.monitored && !ep.library_added).length;
  const seasonAdded = (seasonData?.aired || 0) > 0
    && (seasonData?.catalog_aired || 0) >= (seasonData?.aired || 0);
  const seasonMode = seasonData?.library || seasonData?.monitored ? 'update' : 'add';
  const seasonAction = seasonData?.library || seasonData?.monitored
    ? 'Añadir episodios faltantes'
    : 'Añadir temporada';
  setActiveNav('shows');
  setHeader(series.title || 'Serie');
  document.title = `${series.title || 'Serie'}, TOD`;
  const bgUrl = backdropUrl(series.backdrop_path || series.poster_path, 'w1280');

  const episodes = seasonData?.episodes || [];
  const showSources = episodes.some((ep) => Number(ep.releases) > 0);
  const showProgress = episodes.some((ep) => ep.download === 'descargando' || ep.progress);
  const columns = 3 + (showSources ? 1 : 0) + (showProgress ? 1 : 0);
  const rows = episodes.map((ep) => {
    const tag = `T${String(ep.season).padStart(2, '0')}E${String(ep.episode).padStart(2, '0')}`;
    const name = ep.name || `Episodio ${ep.episode}`;
    const actions = ep.in_catalog
      ? `${!ep.library_added && !ep.monitored ? `<button class="btn btn-sm btn-ghost" data-action="library-title" data-id="${ep.id}" data-key="${esc(ep.key)}" data-title="${esc(name)}" data-releases="${ep.releases || 0}">Añadir</button>` : ''}
         ${!ep.published && !ep.monitored ? `<button class="btn btn-sm btn-ghost" data-action="monitor-title" data-id="${ep.id}">Monitorizar</button>` : ''}
         ${!ep.published && ep.monitored ? `<button class="btn btn-sm btn-ghost" data-action="unmonitor-title" data-id="${ep.id}">Dejar de monitorizar</button>` : ''}
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
            <small>${tag}, ${esc(fmtShortDate(ep.air_date))}${ep.library_added ? ', En biblioteca' : ''}</small>
          </div>
        </a>
      </td>
      <td data-label="Estado">${episodeStatus(ep)}${ep.best_quality ? ` <span class="badge mono">${esc(ep.best_quality)}${ep.best_audio ? ` · ${esc(ep.best_audio)}` : ""}</span>` : ""}</td>
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
      missing ? `${missing} sin añadir` : '',
      incomplete ? `${incomplete} sin fuente` : '',
      upcoming ? `${upcoming} sin estrenar` : '',
    ].filter(Boolean).join(', ');
    const warn = entry.season !== 0 && Boolean(missing || incomplete);
    return `<button class="chip ${entry.season === currentSeason ? 'active' : ''}" data-action="series-season" data-season="${entry.season}">
      ${esc(seasonLabel(entry))} <b>${entry.episode_count || entry.episodes.length}</b>${warn ? ` <span class="warn-dot" title="${esc(note)}"></span>` : ''}
    </button>`;
  }).join('');

  const ss = data.settings || {};
  const profOpts = (data.profiles || []).map((p) => `<option value="${p.id}" ${Number(ss.quality_profile_id) === Number(p.id) ? 'selected' : ''}>${esc(p.name)}</option>`).join('');
  const rfOpts = (data.root_folders || []).map((r) => `<option value="${r.id}" ${Number(ss.root_folder_id) === Number(r.id) ? 'selected' : ''}>${esc(r.name)}</option>`).join('');
  const curTagId = ss.tags?.[0]?.id || '';
  const tagOpts = (data.tags || []).map((t) => `<option value="${t.id}" ${Number(curTagId) === Number(t.id) ? 'selected' : ''}>${esc(t.name)}</option>`).join('');
  const settingsBar = `<details class="adv-box section">
    <summary>${icon('sliders', 'sm')} Ajustes de serie y modo avanzado <span class="tiny faint" style="margin-left:auto">Perfil, carpeta, tipo y presets</span></summary>
    <div class="adv-box-body">
      <div class="series-settings-bar" style="margin-top:0;border:0;padding:0;background:transparent">
        <label class="field"><span>Perfil</span>
          <select class="input input-sm" data-ss="quality_profile_id"><option value="">Por defecto</option>${profOpts}</select>
        </label>
        <label class="field"><span>Carpeta</span>
          <select class="input input-sm" data-ss="root_folder_id"><option value="">Por defecto</option>${rfOpts}</select>
        </label>
        <label class="field"><span>Tipo serie</span>
          <select class="input input-sm" data-ss="series_type">
            <option value="standard" ${ss.series_type === 'standard' ? 'selected' : ''}>Estándar</option>
            <option value="daily" ${ss.series_type === 'daily' ? 'selected' : ''}>Diaria (Daily)</option>
            <option value="anime" ${ss.series_type === 'anime' ? 'selected' : ''}>Anime</option>
          </select>
        </label>
        <label class="field"><span>Etiqueta</span>
          <select class="input input-sm" data-ss="tag_id"><option value="">Ninguna</option>${tagOpts}</select>
        </label>
        <button class="btn btn-sm btn-primary" data-action="series-settings-save" data-tmdb="${tmdb}" style="align-self:end">Guardar ajustes</button>
        <label class="field" style="margin-left:auto"><span>Preset de monitorización</span>
          <select class="input input-sm" data-change="series-preset" data-tmdb="${tmdb}">
            <option value="">— Aplicar preset —</option>
            <option value="all">Todos los episodios</option>
            <option value="future">Solo futuros</option>
            <option value="missing">Solo faltantes</option>
            <option value="first_season">Primera temporada</option>
            <option value="latest_season">Última temporada</option>
            <option value="pilot">Solo piloto (S01E01)</option>
            <option value="none">Ninguno</option>
          </select>
        </label>
      </div>
    </div>
  </details>`;

  $('#view').innerHTML = `
    <div class="breadcrumb"><a href="#/shows">Series</a>${icon('chevron')}<span>${esc(series.title || 'Serie')}</span></div>

    <div class="detail-hero-wrap detail-card">
      ${bgUrl ? `<div class="detail-backdrop" style="background-image:url('${esc(bgUrl)}')"></div><div class="detail-backdrop-scrim"></div>` : ''}
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
          ${ss.series_status ? `<div class="tiny faint" style="margin-top:4px">Estado: ${esc(ss.series_status)}${ss.genres ? ` · ${esc(ss.genres)}` : ''}</div>` : ''}
          <div class="detail-actions">
            ${allAdded ? '' : `<button class="btn btn-primary" data-action="library-series" data-tmdb="${tmdb}" data-title="${esc(series.title || '')}" data-mode="${seriesMode}">${icon('plus')} ${seriesAction}</button>`}
            <button class="btn ${allAdded ? 'btn-primary' : ''}" data-action="sync-series" data-tmdb="${tmdb}">${icon('search')} Buscar fuentes</button>
            ${seriesTrackable && seriesFollowed < seriesTrackable ? `<button class="btn btn-ghost" data-action="monitor-series" data-tmdb="${tmdb}">${icon('plus')} Monitorizar serie</button>` : ''}
            ${seriesFollowed > 0 ? `<button class="btn btn-ghost danger-action" data-action="unmonitor-series" data-tmdb="${tmdb}">Dejar de monitorizar</button>` : ''}
            <a class="btn btn-ghost" href="${esc(data.tmdb_url || '#')}" target="_blank" rel="noreferrer">${icon('external')} TMDB</a>
            ${totals.library ? `<button class="btn btn-sm btn-ghost danger-action" data-action="unpublish-series" data-tmdb="${tmdb}">Quitar</button>` : ''}
          </div>
        </div>
      </div>
    </div>

    ${data.tmdb_error ? `<div class="notice section">No se pudo consultar TMDB. Se muestra solo lo que hay en la base.</div>` : ''}
    ${data.tmdb_errors?.length ? `<div class="notice section">TMDB no devolvió todas las temporadas.</div>` : ''}

    ${settingsBar}

    <div class="record-line section">
      <span><b>${fmtInt(totals.episodes || 0)}</b> episodios</span>
      <span><b>${fmtInt(totals.library || 0)}</b> en biblioteca</span>
      <span><b>${fmtInt(totals.monitored_aired || 0)}</b> monitorizados</span>
      <span><b>${fmtInt(totals.incomplete || 0)}</b> sin fuente</span>
      <span><b>${fmtInt(notAdded)}</b> sin añadir</span>
      ${totals.upcoming ? `<span><b>${fmtInt(totals.upcoming)}</b> sin estrenar</span>` : ''}
      ${totals.specials ? `<span><b>${fmtInt(totals.specials)}</b> especiales</span>` : ''}
    </div>

    <div class="panel section">
      <div class="panel-head">
        <div class="chips">${seasonChips}</div>
        <div class="right row" style="gap:6px">
          ${seasonAdded ? '' : `<button class="btn btn-sm btn-ghost" data-action="library-season" data-season="${currentSeason}" data-tmdb="${tmdb}" data-title="${esc(series.title || '')}" data-mode="${seasonMode}">${icon('plus', 'sm')} ${seasonAction}</button>`}
          ${seasonTrackable && seasonFollowed < seasonTrackable ? `<button class="btn btn-sm btn-ghost" data-action="monitor-season" data-season="${currentSeason}" data-tmdb="${tmdb}">${icon('plus', 'sm')} Monitorizar temporada</button>` : ''}
          ${seasonFollowed > 0 ? `<button class="btn btn-sm btn-ghost danger-action" data-action="unmonitor-season" data-season="${currentSeason}" data-tmdb="${tmdb}">Dejar de monitorizar</button>` : ''}
          <button class="btn btn-sm btn-ghost" data-action="sync-season" data-season="${currentSeason}">${icon('search', 'sm')} Búsqueda automática</button>
          <button class="btn btn-sm btn-ghost" data-action="manual-search-season" data-season="${currentSeason}">${icon('search', 'sm')} Búsqueda interactiva</button>
        </div>
      </div>
      <div class="panel-body" style="padding:0">
        <div class="row between" style="padding:8px 12px;border-bottom:1px solid var(--rule)">
          <span class="tiny muted">${esc(seasonLabel(seasonData || { season: currentSeason }))}</span>
          <span class="row tiny faint" style="gap:14px">
            <span>${fmtInt(seasonData?.episode_count || 0)} episodios</span>
            <span>${fmtInt(seasonData?.library || 0)} en biblioteca</span>
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
