import { api } from '../api.js';
import { esc, fmtBytes, fmtEta, fmtInt, fmtSpeed, pct } from '../format.js';
import { qbitState } from '../labels.js';
import { updateQuery } from '../router.js';
import { state } from '../store.js';
import { $, badge, bar, empty, icon, poster, setHeader, status } from '../ui.js';

function groupKey(item) {
  if (item.media_id) return `media:${item.media_id}`;
  if (item.key) return `key:${item.key}`;
  if (item.info_hash) return `torrent:${item.info_hash}`;
  return `name:${item.title || item.name || 'sin-titulo'}`;
}

function groupItems(items) {
  const groups = new Map();
  for (const item of items) {
    const key = groupKey(item);
    if (!groups.has(key)) {
      groups.set(key, {
        key,
        title: item.title || item.name || 'Sin título',
        poster_path: item.poster_path,
        kind: item.kind,
        season: item.season,
        episode: item.episode,
        keyName: item.key,
        media_id: item.media_id,
        items: [],
        size: 0,
        downloaded: 0,
      });
    }
    const group = groups.get(key);
    group.items.push(item);
    group.size += Number(item.size || 0);
    group.downloaded += Number(item.downloaded || 0);
    if (item.poster_path && !group.poster_path) group.poster_path = item.poster_path;
  }
  return [...groups.values()].map((group) => {
    // `downloaded` de qBittorrent incluye datos desperdiciados y puede
    // superar el tamaño. El progreso fiable es el que informa el torrent.
    const progress = group.items.reduce(
      (sum, item) => sum + Number(item.progress || 0), 0,
    ) / Math.max(1, group.items.length);
    const anyDownloading = group.items.some((item) => ['downloading', 'stalleddl', 'forceddl', 'metadl'].some((value) => String(item.state || '').toLowerCase().includes(value)));
    const anyQueued = group.items.some((item) => String(item.state || '').toLowerCase().includes('queued'));
    const anyMoving = group.items.some((item) => String(item.state || '').toLowerCase().includes('moving'));
    const anyPaused = group.items.some((item) => ['paused', 'stopped'].some((value) => String(item.state || '').toLowerCase().includes(value)));
    const anyError = group.items.some((item) => ['error', 'missing'].some((value) => String(item.state || '').toLowerCase().includes(value)));
    const anySeeding = group.items.some((item) => ['up', 'seed', 'upload'].some((value) => String(item.state || '').toLowerCase().includes(value)));
    const groupState = anyError ? 'error' : anyMoving ? 'moviendo' : anyPaused ? 'pausado' : anyDownloading ? 'descargando' : anyQueued ? 'en_cola' : anySeeding ? 'sembrando' : 'sin_descarga';
    return { ...group, progress, groupState };
  });
}

function groupStatus(group) {
  const map = {
    descargando: ['Descargando', 'info'],
    en_cola: ['En cola', 'muted'],
    moviendo: ['Moviendo a archivo', 'warn'],
    sembrando: ['Sembrando', 'ok'],
    pausado: ['Pausado', 'warn'],
    error: ['Con error', 'bad'],
    sin_descarga: ['Sin descarga', 'muted'],
  };
  const [label, tone] = map[group.groupState] || map.sin_descarga;
  return status(label, tone);
}

const QUALITY_TOKEN = /\b(?:WEB[\s-]?DL|WEBRip|BluRay|BDRip|BRRip|HDTV|DVDRip|2160p|1080p|720p|480p|x265|x264|H\.?264|H\.?265|HEVC|AVC|AAC|AC3|EAC3|DDP?5\.1|Atmos|HDR10?|REMUX|ISO)\b/i;

function prettyName(raw) {
  const text = String(raw || '')
    .replace(/\.[a-z0-9]{2,4}$/i, '')
    .replace(/[._]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  const clean = (value) => value
    .replace(/\[[^\]]*\]?/g, ' ')
    .replace(/\s+/g, ' ')
    .replace(/[\s\-–—:([{]+$/g, '')
    .trim();
  const year = text.match(/\b(?:19|20)\d{2}\b/);
  if (year) {
    return `${clean(text.slice(0, year.index))} (${year[0]})`;
  }
  const cut = text.search(QUALITY_TOKEN);
  return clean(cut > 0 ? text.slice(0, cut) : text) || text;
}

function torrentRow(item, canDelete = false) {
  const stateLabel = qbitState(item.state);
  const selected = state.torrentSelection.has(item.info_hash);
  const linked = Boolean(item.media_id || item.key);
  const display = linked ? (item.release_title || item.name || item.title) : prettyName(item.name);
  const source = item.tracker
    || (item.category && item.category !== 'ondemand' ? item.category : 'Bajo demanda');
  const meta = [source, fmtBytes(item.size)].filter(Boolean).join(', ');
  return `<div class="dl-torrent">
    <input type="checkbox" data-action="torrent-select" data-hash="${esc(item.info_hash)}" ${selected ? 'checked' : ''} aria-label="Seleccionar torrent">
    <div class="name">
      <strong title="${esc(item.name || '')}">${esc(display || '—')}</strong>
      <small>${esc(meta)}</small>
    </div>
    <div class="progress-cell">${bar(item.progress, item.progress >= 1 ? 'green' : '')}<span>${pct(item.progress)}%</span></div>
    <div class="tiny muted nowrap speed">${fmtSpeed(item.dlspeed || 0)}, ${fmtInt(item.num_seeds)} seeds, ETA ${fmtEta(item.eta)}</div>
    <div class="actions">
      ${status(stateLabel.label, stateLabel.tone)}
      <button class="btn btn-sm btn-ghost" data-action="qbit" data-hash="${esc(item.info_hash)}" data-qaction="${String(item.state || '').toLowerCase().includes('paus') || String(item.state || '').toLowerCase().includes('stop') ? 'resume' : 'pause'}" aria-label="${String(item.state || '').toLowerCase().includes('paus') ? 'Reanudar' : 'Pausar'}">${icon(String(item.state || '').toLowerCase().includes('paus') ? 'play' : 'pause', 'sm')}</button>
      ${canDelete ? `<button class="btn btn-sm btn-ghost" data-action="qbit-delete" data-hash="${esc(item.info_hash)}" aria-label="Borrar torrent">${icon('trash', 'sm')}</button>` : ''}
    </div>
  </div>`;
}

export async function renderDownloads({ query, signal }) {
  const text = query.get('q') || '';
  const filter = query.get('state') || '';
  const sort = query.get('sort') || 'recent';
  const [data, seed] = await Promise.all([
    api('/api/downloads?category=ondemand', { signal }),
    api('/api/seed', { signal }).catch(() => null),
  ]);
  const canDelete = Boolean(seed?.defaults?.allow_delete);
  setHeader('Cola');

  const allGroups = groupItems(data.items || []).filter((group) => {
    const haystack = `${group.title} ${group.items.map((item) => item.name || '').join(' ')} ${group.keyName || ''}`.toLowerCase();
    if (text && !haystack.includes(text.toLowerCase())) return false;
    if (filter && group.groupState !== filter) return false;
    return true;
  });
  const counts = {
    '': groupItems(data.items || []).length,
    descargando: groupItems(data.items || []).filter((group) => group.groupState === 'descargando').length,
    en_cola: groupItems(data.items || []).filter((group) => group.groupState === 'en_cola').length,
    moviendo: groupItems(data.items || []).filter((group) => group.groupState === 'moviendo').length,
    sembrando: groupItems(data.items || []).filter((group) => group.groupState === 'sembrando').length,
    pausado: groupItems(data.items || []).filter((group) => group.groupState === 'pausado').length,
    error: groupItems(data.items || []).filter((group) => group.groupState === 'error').length,
  };
  if (sort === 'title') {
    allGroups.sort((a, b) => String(a.title).localeCompare(String(b.title), 'es'));
  } else if (sort === 'size') {
    allGroups.sort((a, b) => b.size - a.size);
  } else if (sort === 'progress') {
    allGroups.sort((a, b) => a.progress - b.progress);
  } else if (sort === 'tracker') {
    const tracker = (group) => String(group.items[0]?.tracker || '');
    allGroups.sort((a, b) => tracker(a).localeCompare(tracker(b), 'es'));
  }

  const groups = allGroups.map((group) => {
    // Un torrent suelto sin vínculo de catálogo no necesita una cabecera
    // que repita su propio nombre y su propio progreso.
    if (group.items.length === 1 && !group.media_id && !group.keyName) {
      return `<div class="dl-group"><div class="dl-torrents">${torrentRow(group.items[0], canDelete)}</div></div>`;
    }
    const tag = group.kind === 'series' && group.season
      ? `T${String(group.season).padStart(2, '0')}E${String(group.episode || 0).padStart(2, '0')}`
      : group.kind === 'movie' ? 'Película' : '';
    const totalRatio = group.items.reduce((sum, item) => sum + Number(item.ratio || 0), 0);
    return `<details class="dl-group" open>
      <summary class="dl-head" style="list-style:none;cursor:pointer">
        <span class="poster">${poster(group.poster_path, group.title, { size: 'w92' })}</span>
        <div class="info">
          <strong>${esc(group.title)}</strong>
          <small>${esc(tag)}, ${group.items.length} torrent${group.items.length === 1 ? '' : 's'}, ${fmtBytes(group.size)}, ratio ${(totalRatio / Math.max(1, group.items.length)).toFixed(2)}</small>
        </div>
        <div class="progress">${bar(group.progress, group.progress >= 1 ? 'green' : '')}</div>
        <div class="side">${groupStatus(group)}${group.media_id ? `<a class="btn btn-sm btn-ghost" href="#/title/${group.media_id}">Abrir</a>` : ''}</div>
      </summary>
      <div class="dl-torrents">${group.items.map((item) => torrentRow(item, canDelete)).join('')}</div>
    </details>`;
  }).join('');

  $('#view').innerHTML = `
    ${data.qbit_error ? `<div class="notice section">${esc(data.qbit_error)}</div>` : ''}
    <div class="toolbar">
      <label class="field grow">
        <span>Buscar</span>
        <input data-input="downloads-q" value="${esc(text)}" placeholder="Título o fichero" autocomplete="off" aria-label="Buscar descargas">
      </label>
      <label class="field" style="width:150px">
        <span>Orden</span>
        <select data-change="downloads-sort" aria-label="Ordenar descargas">
          <option value="recent" ${sort === 'recent' ? 'selected' : ''}>Recientes</option>
          <option value="title" ${sort === 'title' ? 'selected' : ''}>Título</option>
          <option value="size" ${sort === 'size' ? 'selected' : ''}>Tamaño</option>
          <option value="progress" ${sort === 'progress' ? 'selected' : ''}>Progreso</option>
          <option value="tracker" ${sort === 'tracker' ? 'selected' : ''}>Tracker</option>
        </select>
      </label>
    </div>

    <div class="chips section" style="margin-bottom:12px">
      ${[['', 'Todas'], ['descargando', 'Descargando'], ['en_cola', 'En cola'], ['moviendo', 'Moviendo'], ['sembrando', 'Sembrando'], ['pausado', 'Pausadas'], ['error', 'Con error']].map(([value, label]) => `<button class="chip ${filter === value ? 'active' : ''}" data-action="downloads-filter" data-value="${value}">${label} <b>${counts[value] || 0}</b></button>`).join('')}
      <span class="tiny faint" style="margin-left:auto">${allGroups.length} grupos, ${fmtInt(data.items?.length || 0)} torrents</span>
    </div>

    ${groups || `<div class="panel pad">${empty('Sin descargas on-demand', '', `<a class="btn btn-sm btn-ghost" href="#/catalog">Abrir biblioteca</a>`)}</div>`}
  `;
}
