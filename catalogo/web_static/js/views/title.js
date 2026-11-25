import { api } from '../api.js';
import { esc, episodeTag, fmtBytes, fmtDate, fmtInt, pct } from '../format.js';
import { eventLabel, healthStatus, originLabel, WATCH } from '../labels.js';
import { state } from '../store.js';
import { $, badge, bar, empty, icon, poster, setHeader, status } from '../ui.js';
import { renderRichReleaseRow } from './arr_actions.js';

function releaseRow(release, showProgress = false) {
  return renderRichReleaseRow(release, { showProgress, showTrackerLink: true });
}
function _unusedOldReleaseRow(release, showProgress = false) {
  const health = healthStatus(release.health);
  const trackerHref = release.info_url || release.source === 'prowlarr'
    ? `/api/releases/${release.id}/tracker`
    : '';
  const progress = release.torrent_progress !== null && release.torrent_progress !== undefined
    ? `<div class="progress-cell">${bar(release.torrent_progress)}<span>${pct(release.torrent_progress)}%</span></div>`
    : '<span class="faint">—</span>';
  return `<tr>
    <td data-label="Versión"><div class="cell-title"><strong title="${esc(release.title || '')}">${esc(release.title || '—')}</strong><small>${esc(release.tracker || '')}, #${release.id} <span class="badge">${release.privacy === 'public' ? 'público' : 'privado'}</span></small></div></td>
    <td data-label="Calidad">${esc(release.quality || '—')}<div class="tiny faint">${esc(release.audio || '')}</div></td>
    <td data-label="Tamaño" class="num">${fmtBytes(release.size_bytes)}</td>
    <td data-label="Seeds" class="num">${fmtInt(release.seeders)} / ${fmtInt(release.peers)}</td>
    <td data-label="Salud">${status(health.label, health.tone)}</td>
    ${showProgress ? `<td data-label="Progreso">${progress}</td>` : ''}
    <td class="actions" data-label="">
      ${trackerHref ? `<a class="btn btn-sm btn-ghost" href="${trackerHref}" target="_blank" rel="noopener noreferrer" aria-label="Abrir ficha de ${esc(release.title || 'esta versión')} en el tracker">${icon('external', 'sm')} Ficha</a>` : ''}
      <button class="btn btn-sm btn-ghost" data-action="release-target" data-release="${release.id}">Fijar</button>
      <button class="btn btn-sm btn-ghost" data-action="release-health" data-release="${release.id}">Verificar</button>
      <button class="btn btn-sm" data-action="release-prepare" data-release="${release.id}">Preparar</button>
    </td>
  </tr>`;
}


function previewRelease(release) {
  const health = healthStatus(release.health);
  const quality = release.quality || 'Calidad sin identificar';
  return `<div class="title-preview-row">
    <div class="title-preview-main">
      <span class="title-preview-quality">${esc(quality)}</span>
      <small>${esc(release.tracker || 'Origen sin identificar')}${release.audio ? `, ${esc(release.audio)}` : ''}</small>
    </div>
    <span class="tiny nowrap">${fmtInt(release.seeders)} seeds</span>
    ${status(health.label, health.tone)}
    <span class="tiny faint nowrap">${fmtBytes(release.size_bytes)}</span>
  </div>`;
}

function previewFile(file) {
  const path = file.rel_path || file.library_path || file.local_path || file.path || '—';
  const where = file.storage_location === 'LOCAL_DISK'
    ? 'local'
    : file.storage_location === '1FICHIER'
      ? 'archivo'
      : file.source || 'externo';
  return `<div class="title-preview-row files">
    <span class="mono tiny" title="${esc(path)}">${esc(path)}</span>
    <span class="tiny faint nowrap">${fmtBytes(file.size_bytes)}</span>
    <span class="badge">${esc(where)}</span>
  </div>`;
}

function countLabel(count, singular, plural) {
  return `${fmtInt(count)} ${count === 1 ? singular : plural}`;
}

export async function renderTitle({ params, query, signal }) {
  const id = Number(params[0] || 0);
  if (!id) { location.hash = '#/catalog'; return; }
  const data = await api(`/api/titles/${id}`, { signal });
  const item = data.item || {};
  const statusData = data.status || {};
  const od = data.ondemand || {};
  const secondaryBadges = [
    od.library_added ? badge('En Radarr/Sonarr') : '',
    od.downloaded ? badge('Descargada') : '',
  ].filter(Boolean).join('');
  state.titleDetail = data;
  const tab = query.get('tab') || 'resumen';
  const showAside = tab === 'resumen';
  const tag = episodeTag(item);
  const episodeName = item.kind === 'series' ? (item.episode_title || '') : '';
  const heading = episodeName || item.title || '—';
  const synopsis = item.episode_overview || item.overview;
  const seriesContext = episodeName && item.tmdb
    ? `<div class="subtitle"><a href="#/series/${item.tmdb}">${esc(item.title || 'Serie')}</a>${item.year ? ` (${item.year})` : ''}</div>`
    : '';
  setHeader(heading);
  document.title = `${heading}, TOD`;

  const seriesLink = item.kind === 'series' && item.tmdb
    ? `<a href="#/series/${item.tmdb}">${esc(item.title || 'Serie')}</a>${icon('chevron')}`
    : '';
  const alive = data.releases.filter((release) => release.health === 'alive').length;
  const showProgress = data.releases.some((release) => release.torrent_progress !== null && release.torrent_progress !== undefined);
  const streamState = String(statusData.state || '').toUpperCase();
  const streamDownloading = ['DOWNLOADING', 'QUEUED', 'ACTIVATING', 'DOWNLOADING_LOCAL', 'PREPARING']
    .includes(streamState);
  const watch = streamDownloading
    ? {
        label: Number(statusData.progress) > 0
          ? `Descargando ${Math.round(Number(statusData.progress) * 100)}%`
          : 'Descargando',
        tone: 'info',
      }
    : od.published
      ? (alive > 0 || od.has_file || od.downloaded
        ? WATCH.ready
        : { label: 'Sin fuente', tone: 'warn' })
      : od.publication === 'pendiente'
        ? WATCH.pending
        : item.monitored ? WATCH.monitoring : WATCH.unavailable;

  const libraryFiles = data.library || [];
  const fileCount = data.files.length + libraryFiles.length;
  const aliveReleases = (data.releases || []).filter(
    (release) => release.health === 'alive',
  ).length;
  const previewReleases = [...(data.releases || [])]
    .sort((a, b) => (Number(b.health === 'alive') - Number(a.health === 'alive'))
      || ((b.seeders || 0) - (a.seeders || 0)))
    .slice(0, 3);
  const previewFiles = [...(data.files || []), ...libraryFiles].slice(0, 2);

  const strip = `
    <div class="record-line section">
      <span><b>${fmtInt(alive)}</b> ${alive === 1 ? 'fuente viva' : 'fuentes vivas'}</span>
      <span><b>${fmtInt(data.releases.length)}</b> ${data.releases.length === 1 ? 'versión' : 'versiones'}</span>
      <span><b>${fmtInt(fileCount)}</b> ${fileCount === 1 ? 'fichero' : 'ficheros'}</span>
      ${statusData.quality ? `<span><b>${esc(statusData.quality)}</b> calidad</span>` : ''}
      <span><b>${fmtInt(data.commitments.length)}</b> ${data.commitments.length === 1 ? 'compromiso' : 'compromisos'}</span>
      <span><b>${fmtInt(data.events.length)}</b> ${data.events.length === 1 ? 'evento' : 'eventos'}</span>
    </div>
  `;

  const profOpts = (data.profiles || []).map((p) => `<option value="${p.id}" ${Number(item.quality_profile_id) === Number(p.id) ? 'selected' : ''}>${esc(p.name)}</option>`).join('');
  const rfOpts = (data.root_folders || []).map((r) => `<option value="${r.id}" ${Number(item.root_folder_id) === Number(r.id) ? 'selected' : ''}>${esc(r.name)}</option>`).join('');
  const curTagId = data.tags?.[0]?.id || '';
  const tagOpts = (data.all_tags || []).map((t) => `<option value="${t.id}" ${Number(curTagId) === Number(t.id) ? 'selected' : ''}>${esc(t.name)}</option>`).join('');
  const aside = `
    <div class="panel">
      <div class="panel-head"><h2>Detalles</h2></div>
      <div class="panel-body" style="padding-top:6px;padding-bottom:8px">
        ${statusData.quality ? `<div class="kv"><span>Calidad</span><span>${esc(statusData.quality)}</span></div>` : ''}
        ${statusData.audio ? `<div class="kv"><span>Audio</span><span>${esc(statusData.audio)}</span></div>` : ''}
        <div class="kv"><span>Procedencia</span><span>${esc(originLabel(item.origin))}${item.origin_detail ? `<span class="tiny faint">, ${esc(item.origin_detail)}</span>` : ''}</span></div>
        <div class="kv"><span>Ubicación</span><span>${od.has_file ? 'Radarr/Sonarr' : od.downloaded ? 'Descarga' : '—'}</span></div>
        <div class="kv"><span>Fuentes</span><span>${countLabel(alive, 'viva', 'vivas')} de ${countLabel(data.releases.length, 'versión', 'versiones')}</span></div>
        ${od.download === 'descargando' || od.download === 'pausado' || od.download === 'error' ? `<div class="kv"><span>Descarga</span><span>${od.download === 'descargando' ? status('Descargando', 'info') : od.download === 'pausado' ? status('Pausado', 'warn') : status('Error', 'bad')}</span></div>` : ''}
      </div>
    </div>
    <div class="panel title-settings-box">
      <div class="panel-head"><h2>Perfil y carpeta</h2></div>
      <div class="panel-body stack" style="gap:8px">
        <label class="field"><span>Perfil de Calidad</span>
          <select class="input input-sm" data-ts="quality_profile_id"><option value="">Por defecto</option>${profOpts}</select>
        </label>
        <label class="field"><span>Carpeta</span>
          <select class="input input-sm" data-ts="root_folder_id"><option value="">Por defecto</option>${rfOpts}</select>
        </label>
        ${item.kind === 'movie' ? `<label class="field"><span>Disponibilidad mínima</span>
          <select class="input input-sm" data-ts="minimum_availability">
            <option value="announced" ${item.minimum_availability === 'announced' ? 'selected' : ''}>Anunciada</option>
            <option value="in_cinemas" ${item.minimum_availability === 'in_cinemas' ? 'selected' : ''}>En cines</option>
            <option value="released" ${(item.minimum_availability || 'released') === 'released' ? 'selected' : ''}>Estrenada / Digital</option>
          </select>
        </label>` : ''}
        <label class="field"><span>Etiqueta</span>
          <select class="input input-sm" data-ts="tag_id"><option value="">Ninguna</option>${tagOpts}</select>
        </label>
        <button class="btn btn-sm btn-primary" data-action="title-settings-save" data-id="${item.id}">Guardar ajustes</button>
      </div>
    </div>
  `;

  const overview = `
    <div class="panel flush">
      <div class="panel-head">
        <h2>Versiones</h2>
        <div class="right row" style="gap:8px">
          <button class="btn btn-sm btn-ghost" data-action="detail-tab" data-tab="fuentes">Ver todas</button>
        </div>
      </div>
      ${previewReleases.length
        ? `<div class="title-preview">${previewReleases.map(previewRelease).join('')}</div>`
        : empty('Sin versiones', 'Busca fuentes para capturar candidatos.', `<button class="btn btn-sm" data-action="manual-search" data-id="${id}" data-title="${esc(item.title || '')}">Buscar manualmente</button>`)}
    </div>
    <div class="panel flush section">
      <div class="panel-head">
        <h2>Ficheros</h2>
        <div class="right row" style="gap:8px">
          <span class="tiny faint">${countLabel(fileCount, 'fichero', 'ficheros')}</span>
          <button class="btn btn-sm btn-ghost" data-action="detail-tab" data-tab="ficheros">Ver todos</button>
        </div>
      </div>
      ${previewFiles.length
        ? `<div class="title-preview">${previewFiles.map(previewFile).join('')}</div>`
        : empty(
          'Sin ficheros',
          aliveReleases
            ? `Hay ${countLabel(aliveReleases, 'fuente viva', 'fuentes vivas')} sin descargar. Abre Fuentes y pulsa Preparar.`
            : 'No hay copias locales ni de archivo indexadas.',
          aliveReleases
            ? '<button class="btn btn-sm" data-action="detail-tab" data-tab="fuentes">Ver fuentes</button>'
            : '',
        )}
    </div>
  `;

  const releases = `
    <div class="panel flush">
      <div class="panel-head">
        <h2>Versiones disponibles</h2>
        <div class="right row" style="gap:8px">
          <button class="btn btn-sm" data-action="manual-search" data-id="${id}" data-title="${esc(item.title || '')}">Buscar manualmente</button>
        </div>
      </div>
      ${data.releases.length ? `<div class="table-wrap"><table class="table--stack releases-table">
        
        <thead><tr><th>Versión</th><th>Calidad</th><th class="num">Score</th><th class="num">Tamaño</th><th class="num">Seeds</th><th>Salud</th>${showProgress ? '<th>Progreso</th>' : ''}<th></th></tr></thead>
        <tbody>${data.releases.map((release) => releaseRow(release, showProgress)).join('')}</tbody>
      </table></div>` : empty('Sin versiones', 'Busca fuentes para capturar candidatos.', `<button class="btn btn-sm" data-action="manual-search" data-id="${id}" data-title="${esc(item.title || '')}">Buscar manualmente</button>`)}
    </div>
  `;

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
        ${!data.files.length && !libraryFiles.length ? empty(
          'Sin ficheros',
          aliveReleases
            ? `Hay ${fmtInt(aliveReleases)} ${aliveReleases === 1 ? 'fuente viva' : 'fuentes vivas'} sin descargar. Abre Fuentes y pulsa Preparar.`
            : 'No hay copias locales ni de archivo indexadas.',
          aliveReleases
            ? '<button class="btn btn-sm" data-action="detail-tab" data-tab="fuentes">Ver fuentes</button>'
            : '',
        ) : ''}
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

    <div class="panel pad detail-card">
      <div class="detail-hero">
        <div class="poster">${poster(data.poster_path, item.title)}</div>
        <div class="info">
          <h2>${esc(heading)} ${item.year && (item.kind === 'movie' || !episodeName) ? `<span class="muted" style="font-size:16px;font-weight:400">(${item.year})</span>` : ''}</h2>
          ${seriesContext}
          <div class="row">
            ${tag ? badge(tag, 'mono') : ''}
            ${status(watch.label, watch.tone)}
            ${od.download === 'descargando' ? status(`Descargando ${pct(od.progress || 0)}%`, 'info') : ''}
            ${secondaryBadges}
          </div>
          ${synopsis ? `<p class="overview">${esc(synopsis)}</p>` : ''}
          ${data.collection ? `<a class="collection-link" href="#/collection/${data.collection.id}">
            <span class="tiny">Colección</span>
            <strong>${esc(data.collection.name)}</strong>
            <span class="tiny faint">${fmtInt(data.collection.parts_count || 0)} películas</span>
          </a>` : ''}
          <div class="detail-actions">
            ${!od.library_added && !item.monitored ? `<button class="btn btn-primary" data-action="library-title" data-id="${item.id}" data-key="${esc(item.key)}" data-title="${esc(item.title || '')}" data-releases="${data.releases.length}">${icon('plus')} Catalogar en TOD</button>` : ''}
            ${od.library_added && !od.published && item.monitored ? `<button class="btn btn-ghost" data-action="unmonitor-title" data-id="${item.id}">Dejar de seguir</button>` : ''}
            ${od.library_added && !od.published && !item.monitored && !od.has_file && !od.downloaded ? `<button class="btn" data-action="monitor-title" data-id="${item.id}">Seguir</button>` : ''}
            ${!od.downloaded && data.releases.length ? `<button class="btn" data-action="download-now" data-key="${esc(item.key)}">${icon('download')} Descargar ahora</button>` : ''}
            <button class="btn btn-ghost" data-action="manual-search" data-id="${item.id}" data-title="${esc(item.title || '')}">${icon('search')} Buscar fuentes</button>
            <button class="btn btn-ghost" data-action="sync-title" data-id="${item.id}">${icon('search')} Buscar y añadir</button>
            ${item.tmdb ? `<a class="btn btn-ghost" href="${esc(data.tmdb_url || '#')}" target="_blank" rel="noreferrer">${icon('external')} TMDB</a>` : ''}
            ${od.published ? `<button class="btn btn-sm btn-ghost danger-action" data-action="unpublish-title" data-id="${item.id}">Quitar de TOD</button>` : ''}
          </div>
        </div>
      </div>
    </div>

    ${strip}

    <div class="title-layout ${showAside ? '' : 'title-layout-full'}">
      <div class="title-main">
        <div class="tabs" role="tablist">
          <button class="${tab === 'resumen' ? 'active' : ''}" data-action="detail-tab" data-tab="resumen" role="tab" aria-selected="${tab === 'resumen'}">Resumen</button>
          <button class="${tab === 'fuentes' ? 'active' : ''}" data-action="detail-tab" data-tab="fuentes" role="tab" aria-selected="${tab === 'fuentes'}">Fuentes <b>${data.releases.length}</b></button>
          <button class="${tab === 'ficheros' ? 'active' : ''}" data-action="detail-tab" data-tab="ficheros" role="tab" aria-selected="${tab === 'ficheros'}">Ficheros <b>${fileCount}</b></button>
          <button class="${tab === 'historial' ? 'active' : ''}" data-action="detail-tab" data-tab="historial" role="tab" aria-selected="${tab === 'historial'}">Historial <b>${data.events.length}</b></button>
        </div>

        ${tab === 'fuentes' ? releases : tab === 'ficheros' ? files : tab === 'historial' ? history : overview}
      </div>
      ${showAside ? `<aside class="title-aside" aria-label="Detalles del título">${aside}</aside>` : ''}
    </div>
  `;
}
