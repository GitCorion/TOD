import { api } from '../api.js';
import { esc, episodeTag, fmtBytes, fmtDate, fmtInt, pct } from '../format.js';
import { eventLabel, healthStatus } from '../labels.js';
import { updateQuery } from '../router.js';
import { state } from '../store.js';
import { $, badge, bar, empty, icon, poster, setHeader, status } from '../ui.js';

function releaseRow(release) {
  const health = healthStatus(release.health);
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
      <a class="btn btn-sm btn-ghost" href="/api/releases/${release.id}/torrent" download aria-label="Descargar .torrent de ${esc(release.title || 'esta versión')}">${icon('download', 'sm')} Torrent</a>
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
  setHeader(item.title || 'Ficha', item.kind === 'movie' ? 'Película' : 'Episodio');

  const seriesLink = item.kind === 'series' && item.tmdb
    ? `<a href="#/series/${item.tmdb}">${esc(item.title || 'Serie')}</a>${icon('chevron')}`
    : '';  const alive = data.releases.filter((release) => release.health === 'alive').length;
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
    <div class="grid grid-2">
      <div class="panel">
        <div class="panel-head"><h2>Estado</h2>${liveState}</div>
        <div class="panel-body" style="padding-top:6px;padding-bottom:8px">
          <div class="kv"><span>En biblioteca</span><span>${od.published ? status('Sí', 'ok') : status('No', 'muted')}</span></div>
          <div class="kv"><span>Descargado</span><span>${od.downloaded ? status('Sí', 'ok') : status('No', 'muted')}</span></div>
          <div class="kv"><span>Descarga on-demand</span><span>${od.download === 'descargando' ? status('Descargando', 'info') : od.download === 'pausado' ? status('Pausado', 'warn') : od.download === 'error' ? status('Error', 'bad') : od.downloaded ? status('Completada', 'ok') : status('Sin descarga', 'muted')}</span></div>
          <div class="kv"><span>Radarr / Sonarr</span><span>${od.has_file ? status('Con fichero', 'ok') : od.in_library ? status('En biblioteca', 'info') : status('No', 'muted')}</span></div>
          <div class="kv"><span>Puntero</span><span>${esc(statusData.source || '—')} ${statusData.reason ? `<span class="faint">(${esc(statusData.reason)})</span>` : ''}</span></div>
          <div class="kv"><span>Calidad / audio</span><span>${esc(statusData.quality || '—')}, ${esc(statusData.audio || '—')}</span></div>
          <div class="kv"><span>Progreso</span><span>${pct(statusData.progress || 0)} %</span></div>
        </div>
      </div>
      <div class="panel">
        <div class="panel-head"><h2>Resumen técnico</h2><span class="right tiny faint">solo lectura</span></div>
        <div class="panel-body" style="padding-top:6px;padding-bottom:8px">
          <div class="kv"><span>Versiones</span><span>${data.releases.length} (${data.releases.filter((release) => release.health === 'alive').length} vivas)</span></div>
          <div class="kv"><span>Ficheros</span><span>${data.files.length} (${data.files.filter((file) => file.storage_location === 'LOCAL_DISK').length} locales)</span></div>
          <div class="kv"><span>Puntero</span><span>${data.target ? `${esc(data.target.source_kind)} ${data.target.release_id ? `release ${data.target.release_id}` : ''}` : 'auto'}</span></div>
          <div class="kv"><span>Biblioteca</span><span>${data.library.length ? esc(data.library[0].source) : 'no'}</span></div>
          <div class="kv"><span>Clave</span><span class="mono">${esc(item.key)}</span></div>
          <div class="kv"><span>Actualizado</span><span>${fmtDate(item.updated_at)}</span></div>
          <div class="kv"><span>Compromiso</span><span>${esc(statusData.seed_state || '—')} ${statusData.seed_required_until ? `<span class="faint">hasta ${fmtDate(statusData.seed_required_until)}</span>` : ''}</span></div>
        </div>
      </div>
    </div>

    <div class="panel flush section">
      <div class="panel-head"><h2>Últimos eventos</h2><a class="right tiny" href="#/title/${id}?tab=historial">Ver todos</a></div>
      ${data.events.length ? `<div class="table-wrap"><table class="table--stack">
        <thead><tr><th>Fecha</th><th>Acción</th><th>Datos</th></tr></thead>
        <tbody>${data.events.slice(0, 6).map((event) => `<tr><td data-label="Fecha">${fmtDate(event.ts)}</td><td data-label="Acción">${badge(eventLabel(event.kind))}</td><td data-label="Datos"><span class="event-payload">${esc(event.payload || '')}</span></td></tr>`).join('')}</tbody>
      </table></div>` : empty('Sin eventos', 'Todavía no hay actividad para este título.')}
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
        </table></div>` : empty('Sin ficheros', 'No hay copias locales ni de archivo indexadas.')}
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
    <div class="breadcrumb"><a href="#/catalog">Catálogo</a>${icon('chevron')}${seriesLink}<span>${esc(tag || item.title || 'Ficha')}</span></div>

    <div class="panel pad">
      <div class="detail-hero">
        <div class="poster">${poster(data.poster_path, item.title)}</div>
        <div class="info">
          <h2>${esc(item.title || '—')} ${item.year ? `<span class="muted" style="font-size:16px;font-weight:400">(${item.year})</span>` : ''}</h2>
          <div class="row">
            ${badge(item.kind === 'movie' ? 'película' : 'episodio', 'mono')}
            ${tag ? badge(tag, 'mono') : ''}
            ${od.library_added
              ? status(od.published ? 'En biblioteca' : 'En biblioteca, sin enlace', od.published ? 'ok' : 'warn')
              : status('Sin añadir', 'muted')}
            ${item.monitored ? status(od.available === false ? 'Pendiente de estreno' : 'En seguimiento', od.available === false ? 'muted' : 'info') : ''}
            ${od.downloaded ? status('Descargada', 'ok') : ''}
            ${od.download === 'descargando' ? status('Descargando', 'info') : ''}
          </div>
          ${data.collection ? `<a class="collection-link" href="#/collection/${data.collection.id}">
            <span class="tiny">Colección</span>
            <strong>${esc(data.collection.name)}</strong>
            <span class="tiny faint">${fmtInt(data.collection.parts_count || 0)} películas</span>
          </a>` : ''}
          <div class="detail-actions">
            ${!od.library_added ? `<button class="btn btn-primary" data-action="library-title" data-id="${item.id}" data-key="${esc(item.key)}" data-title="${esc(item.title || '')}" data-releases="${data.releases.length}">${icon('plus')} Añadir a la biblioteca</button>` : ''}
            ${od.library_added && !od.published && item.monitored ? `<button class="btn btn-danger" data-action="unmonitor-title" data-id="${item.id}">Quitar seguimiento</button>` : ''}
            ${od.library_added && !od.published && !item.monitored ? `<button class="btn" data-action="monitor-title" data-id="${item.id}">Monitorizar</button>` : ''}
            ${!od.downloaded && data.releases.length ? `<button class="btn" data-action="download-now" data-key="${esc(item.key)}">${icon('download')} Forzar descarga</button>` : ''}
            ${od.library_added || od.published ? `<button class="btn btn-danger" data-action="unpublish-title" data-id="${item.id}">Quitar de la biblioteca</button>` : ''}
            <button class="btn btn-ghost" data-action="manual-search" data-id="${item.id}" data-title="${esc(item.title || '')}">${icon('search')} Buscar manualmente</button>
            <button class="btn btn-ghost" data-action="sync-title" data-id="${item.id}">${icon('search')} Buscar fuentes</button>
            ${item.tmdb ? `<a class="btn btn-ghost" href="${esc(data.tmdb_url || '#')}" target="_blank" rel="noreferrer">${icon('external')} TMDB</a>` : ''}
          </div>
        </div>
      </div>
    </div>

    <div class="tabs" role="tablist">
      <button class="${tab === 'resumen' ? 'active' : ''}" data-action="detail-tab" data-tab="resumen" role="tab" aria-selected="${tab === 'resumen'}">Resumen</button>
      <button class="${tab === 'fuentes' ? 'active' : ''}" data-action="detail-tab" data-tab="fuentes" role="tab" aria-selected="${tab === 'fuentes'}">Fuentes <b>${data.releases.length}</b></button>
      <button class="${tab === 'ficheros' ? 'active' : ''}" data-action="detail-tab" data-tab="ficheros" role="tab" aria-selected="${tab === 'ficheros'}">Ficheros <b>${data.files.length}</b></button>
      <button class="${tab === 'historial' ? 'active' : ''}" data-action="detail-tab" data-tab="historial" role="tab" aria-selected="${tab === 'historial'}">Historial <b>${data.events.length}</b></button>
    </div>

    ${tab === 'fuentes' ? releases : tab === 'ficheros' ? files : tab === 'historial' ? history : summary}
  `;
}
