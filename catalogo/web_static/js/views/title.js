import { api } from '../api.js';
import { esc, episodeTag, fmtBytes, fmtDate, fmtInt, pct } from '../format.js';
import { eventLabel, healthStatus, originLabel } from '../labels.js';
import { updateQuery } from '../router.js';
import { state } from '../store.js';
import { $, badge, bar, empty, icon, poster, setHeader, status } from '../ui.js';

function releaseRow(release) {
  const health = healthStatus(release.health);
  const trackerHref = release.info_url || release.source === 'prowlarr'
    ? `/api/releases/${release.id}/tracker`
    : '';
  const progress = release.torrent_progress !== null && release.torrent_progress !== undefined
    ? `<div class="progress-cell">${bar(release.torrent_progress)}<span>${pct(release.torrent_progress)}%</span></div>`
    : '<span class="faint">—</span>';
  return `<tr>
    <td data-label="Versión"><div class="cell-title"><strong>${esc(release.title || '—')}</strong><small>${esc(release.tracker || '')}, #${release.id}</small></div></td>
    <td data-label="Calidad">${esc(release.quality || '—')}<div class="tiny faint">${esc(release.audio || '')}</div></td>
    <td data-label="Tamaño" class="num">${fmtBytes(release.size_bytes)}</td>
    <td data-label="Seeds" class="num">${fmtInt(release.seeders)} / ${fmtInt(release.peers)}</td>
    <td data-label="Salud">${status(health.label, health.tone)}</td>
    <td data-label="Privacidad">${badge(release.privacy === 'public' ? 'público' : 'privado', 'mono')} ${release.debrid_cached ? badge('debrid', 'mono') : ''}</td>
    <td data-label="Progreso">${progress}</td>
    <td class="actions" data-label="">
      ${trackerHref ? `<a class="btn btn-sm btn-ghost" href="${trackerHref}" target="_blank" rel="noopener noreferrer" aria-label="Abrir ficha de ${esc(release.title || 'esta versión')} en el tracker">${icon('external', 'sm')} Ficha</a>` : ''}
      <button class="btn btn-sm btn-ghost" data-action="release-target" data-release="${release.id}">Fijar</button>
      <button class="btn btn-sm btn-ghost" data-action="release-health" data-release="${release.id}">Verificar</button>
      <button class="btn btn-sm" data-action="release-prepare" data-release="${release.id}">Preparar</button>
    </td>
  </tr>`;
}

export async function renderTitle({ params, query, signal }) {
  const id = Number(params[0] || 0);
  if (!id) { location.hash = '#/catalog'; return; }
  const data = await api(`/api/titles/${id}`, { signal });
  const item = data.item || {};
  const statusData = data.status || {};
  const od = data.ondemand || {};
  state.titleDetail = data;
  const tab = query.get('tab') || 'resumen';
  const tag = episodeTag(item);
  const pageLabel = item.kind === 'movie' ? 'Película' : 'Episodio';
  const episodeName = item.kind === 'series' ? (item.episode_title || '') : '';
  const heading = episodeName || item.title || '—';
  const synopsis = item.episode_overview || item.overview;
  const seriesContext = episodeName && item.tmdb
    ? `<div class="subtitle"><a href="#/series/${item.tmdb}">${esc(item.title || 'Serie')}</a>${item.year ? ` (${item.year})` : ''}</div>`
    : '';
  setHeader(pageLabel, 'Biblioteca');
  document.title = `${heading}, TOD`;

  const seriesLink = item.kind === 'series' && item.tmdb
    ? `<a href="#/series/${item.tmdb}">${esc(item.title || 'Serie')}</a>${icon('chevron')}`
    : '';
  const alive = data.releases.filter((release) => release.health === 'alive').length;
  const unverified = data.releases.some((release) => ['unknown', 'stale'].includes(release.health));
  const liveState = statusData.stream_ready
    ? status('Reproducible', 'ok')
    : alive
      ? status(`${alive} fuentes vivas`, 'info')
      : data.releases.length
        ? unverified
          ? status('Candidatos sin verificar', 'warn')
          : status('Sin fuentes vivas', 'bad')
        : status('Sin fuente', 'muted');

  const summary = `
    <div class="panel">
      <div class="panel-head"><h2>Detalles</h2>${liveState}</div>
      <div class="panel-body" style="padding-top:6px;padding-bottom:8px">
        <div class="kv"><span>Calidad</span><span>${esc(statusData.quality || '—')}</span></div>
        <div class="kv"><span>Audio</span><span>${esc(statusData.audio || '—')}</span></div>
        <div class="kv"><span>Procedencia</span><span>${esc(originLabel(item.origin))}${item.origin_detail ? `<span class="tiny faint"> · ${esc(item.origin_detail)}</span>` : ''}</span></div>
        <div class="kv"><span>Custodia</span><span>${od.has_file ? 'Radarr/Sonarr' : od.downloaded ? 'Descarga' : '—'}</span></div>
        ${od.download === 'descargando' || od.download === 'pausado' || od.download === 'error' ? `<div class="kv"><span>Descarga</span><span>${od.download === 'descargando' ? status('Descargando', 'info') : od.download === 'pausado' ? status('Pausado', 'warn') : status('Error', 'bad')}</span></div>` : ''}
      </div>
    </div>
  `;

  const releases = `
    <div class="panel flush">
      <div class="panel-head">
        <h2>Versiones disponibles</h2>
        <div class="right row" style="gap:8px">
          <span class="tiny faint">${data.releases.length} releases</span>
          <button class="btn btn-sm" data-action="manual-search" data-id="${id}" data-title="${esc(item.title || '')}">Buscar manualmente</button>
        </div>
      </div>
      ${data.releases.length ? `<div class="table-wrap"><table class="table--stack">
        <thead><tr><th>Versión</th><th>Calidad</th><th>Tamaño</th><th>Seeds</th><th>Salud</th><th>Privacidad</th><th>Progreso</th><th></th></tr></thead>
        <tbody>${data.releases.map(releaseRow).join('')}</tbody>
      </table></div>` : empty('Sin versiones', 'Busca fuentes para capturar candidatos.', `<button class="btn btn-sm" data-action="manual-search" data-id="${id}" data-title="${esc(item.title || '')}">Buscar manualmente</button>`)}
    </div>
  `;

  const libraryFiles = data.library || [];
  const files = `
    <div class="grid grid-2">
      <div class="panel flush">
        <div class="panel-head"><h2>Ficheros</h2></div>
        ${data.files.length ? `<div class="table-wrap"><table class="table--stack">
          <thead><tr><th>#</th><th>Ruta</th><th>Tamaño</th><th>Ubicación</th></tr></thead>
          <tbody>${data.files.map((file) => `<tr>
            <td data-label="#">${file.file_index ?? '—'}</td>
            <td data-label="Ruta"><span class="mono tiny">${esc(file.rel_path || file.library_path || file.local_path || '—')}</span></td>
            <td data-label="Tamaño">${fmtBytes(file.size_bytes)}</td>
            <td data-label="Ubicación">${badge(file.storage_location === 'LOCAL_DISK' ? 'local' : file.storage_location === '1FICHIER' ? 'archivo' : '—', 'mono')} ${badge(file.state, file.state === 'missing' ? 'mono' : 'mono')}</td>
          </tr>`).join('')}</tbody>
        </table></div>` : ''}
        ${libraryFiles.length ? `<div class="table-wrap" style="margin-top:${data.files.length ? '10px' : '0'}"><table class="table--stack">
          <thead><tr><th>Ruta externa</th><th>Tamaño</th><th>Origen</th></tr></thead>
          <tbody>${libraryFiles.map((file) => `<tr>
            <td data-label="Ruta"><span class="mono tiny">${esc(file.path || '—')}</span></td>
            <td data-label="Tamaño">${fmtBytes(file.size_bytes)}</td>
            <td data-label="Origen">${badge(file.source || 'externo', 'mono')}</td>
          </tr>`).join('')}</tbody>
        </table></div>` : ''}
        ${!data.files.length && !libraryFiles.length ? empty('Sin ficheros', 'No hay copias locales ni de archivo indexadas.') : ''}
      </div>
      <div class="panel flush">
        <div class="panel-head"><h2>Compromisos de siembra</h2></div>
        ${data.commitments.length ? `<div class="table-wrap"><table class="table--stack">
          <thead><tr><th>#</th><th>Tracker</th><th>Estado</th><th>Ratio</th><th>Tamaño</th><th>Hasta</th><th></th></tr></thead>
          <tbody>${data.commitments.map((commitment) => `<tr>
            <td data-label="#">#${commitment.id}</td>
            <td data-label="Tracker">${esc(commitment.tracker)}</td>
            <td data-label="Estado">${status(commitment.state, commitment.state === 'seeding' ? 'ok' : 'muted')}</td>
            <td data-label="Ratio">${(commitment.live_ratio ?? 0).toFixed(2)}</td>
            <td data-label="Tamaño">${fmtBytes(commitment.size_bytes)}</td>
            <td data-label="Hasta">${fmtDate(commitment.seed_required_until)}</td>
            <td class="actions" data-label="">${commitment.state === 'seeding' ? `<button class="btn btn-sm" data-action="seed-finish" data-id="${commitment.id}">Cerrar</button>` : ''}</td>
          </tr>`).join('')}</tbody>
        </table></div>` : empty('Sin compromisos', 'Las descargas nuevas crean uno al prepararse.')}
      </div>
    </div>
  `;

  const history = `
    <div class="panel flush">
      <div class="panel-head"><h2>Historial del título</h2><span class="right tiny faint">${data.events.length} eventos</span></div>
      ${data.events.length ? `<div class="table-wrap"><table class="table--stack">
        <thead><tr><th>Fecha</th><th>Acción</th><th>Datos</th></tr></thead>
        <tbody>${data.events.map((event) => `<tr><td data-label="Fecha">${fmtDate(event.ts)}</td><td data-label="Acción">${badge(eventLabel(event.kind))}</td><td data-label="Datos"><span class="event-payload">${esc(event.payload || '')}</span></td></tr>`).join('')}</tbody>
      </table></div>` : empty('Sin eventos', 'Todavía no hay actividad para este título.')}
    </div>
  `;

  $('#view').innerHTML = `
    <div class="breadcrumb"><a href="#/catalog">Biblioteca</a>${icon('chevron')}${seriesLink}<span>${esc(tag || item.title || 'Ficha')}</span></div>

    <div class="panel pad">
      <div class="detail-hero">
        <div class="poster">${poster(data.poster_path, item.title)}</div>
        <div class="info">
          <h2>${esc(heading)} ${item.year && (item.kind === 'movie' || !episodeName) ? `<span class="muted" style="font-size:16px;font-weight:400">(${item.year})</span>` : ''}</h2>
          ${seriesContext}
          <div class="row">
            ${badge(item.kind === 'movie' ? 'película' : 'episodio', 'mono')}
            ${tag ? badge(tag, 'mono') : ''}
            ${od.published ? status('En Jellyfin', 'ok') : status('Sin .strm', 'muted')}
            ${od.library_added ? status('En Radarr/Sonarr', 'info') : ''}
            ${item.monitored
              ? status(od.available === false ? 'Sin estrenar' : 'Monitorizado', od.available === false ? 'muted' : 'info')
              : status('Sin monitorizar', 'muted')}
            ${od.downloaded ? status('Descargada', 'ok') : ''}
            ${od.download === 'descargando' ? status('Descargando', 'info') : ''}
          </div>
          ${synopsis ? `<p class="overview">${esc(synopsis)}</p>` : ''}
          ${data.collection ? `<a class="collection-link" href="#/collection/${data.collection.id}">
            <span class="tiny">Colección</span>
            <strong>${esc(data.collection.name)}</strong>
            <span class="tiny faint">${fmtInt(data.collection.parts_count || 0)} películas</span>
          </a>` : ''}
          <div class="detail-actions">
            ${!od.library_added && !item.monitored ? `<button class="btn btn-primary" data-action="library-title" data-id="${item.id}" data-key="${esc(item.key)}" data-title="${esc(item.title || '')}" data-releases="${data.releases.length}">${icon('plus')} Catalogar en TOD</button>` : ''}
            ${od.library_added && !od.published && item.monitored ? `<button class="btn btn-danger" data-action="unmonitor-title" data-id="${item.id}">Dejar de monitorizar</button>` : ''}
            ${od.library_added && !od.published && !item.monitored && !od.has_file && !od.downloaded ? `<button class="btn" data-action="monitor-title" data-id="${item.id}">Monitorizar</button>` : ''}
            ${!od.downloaded && data.releases.length ? `<button class="btn" data-action="download-now" data-key="${esc(item.key)}">${icon('download')} Descargar ahora</button>` : ''}
            ${od.published ? `<button class="btn btn-danger" data-action="unpublish-title" data-id="${item.id}">Quitar de Jellyfin</button>` : ''}
            <button class="btn btn-ghost" data-action="manual-search" data-id="${item.id}" data-title="${esc(item.title || '')}">${icon('search')} Buscar manualmente</button>
            <button class="btn btn-ghost" data-action="sync-title" data-id="${item.id}">${icon('search')} Buscar y publicar</button>
            ${item.tmdb ? `<a class="btn btn-ghost" href="${esc(data.tmdb_url || '#')}" target="_blank" rel="noreferrer">${icon('external')} TMDB</a>` : ''}
          </div>
        </div>
      </div>
    </div>

    <div class="tabs" role="tablist">
      <button class="${tab === 'resumen' ? 'active' : ''}" data-action="detail-tab" data-tab="resumen" role="tab" aria-selected="${tab === 'resumen'}">Resumen</button>
      <button class="${tab === 'fuentes' ? 'active' : ''}" data-action="detail-tab" data-tab="fuentes" role="tab" aria-selected="${tab === 'fuentes'}">Fuentes <b>${data.releases.length}</b></button>
      <button class="${tab === 'ficheros' ? 'active' : ''}" data-action="detail-tab" data-tab="ficheros" role="tab" aria-selected="${tab === 'ficheros'}">Ficheros <b>${data.files.length + libraryFiles.length}</b></button>
      <button class="${tab === 'historial' ? 'active' : ''}" data-action="detail-tab" data-tab="historial" role="tab" aria-selected="${tab === 'historial'}">Historial <b>${data.events.length}</b></button>
    </div>

    ${tab === 'fuentes' ? releases : tab === 'ficheros' ? files : tab === 'historial' ? history : summary}
  `;
}
