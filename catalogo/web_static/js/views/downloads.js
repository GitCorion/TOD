import { api } from '../api.js';
import { esc, fmtBytes, fmtEta, fmtInt, fmtSpeed, pct } from '../format.js';
import { qbitState } from '../labels.js';
import { updateQuery } from '../router.js';
import { state } from '../store.js';
import { $, badge, bar, empty, icon, poster, setHeader, status } from '../ui.js';

function groupKey(item) {
  return item.media_id ? `media:${item.media_id}` : item.key ? `key:${item.key}` : `name:${item.title || item.name}`;
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
    const progress = group.size ? group.downloaded / group.size : (
      group.items.reduce((sum, item) => sum + Number(item.progress || 0), 0) / group.items.length
    );
    const anyDownloading = group.items.some((item) => String(item.state || '').toLowerCase().includes('dl') || String(item.state || '').toLowerCase().includes('download'));
    const anyPaused = group.items.some((item) => ['paused', 'stopped'].some((value) => String(item.state || '').toLowerCase().includes(value)));
    const anyError = group.items.some((item) => ['error', 'missing'].some((value) => String(item.state || '').toLowerCase().includes(value)));
    const anySeeding = group.items.some((item) => ['up', 'seed', 'upload'].some((value) => String(item.state || '').toLowerCase().includes(value)));
    const groupState = anyError ? 'error' : anyPaused ? 'pausado' : anyDownloading ? 'descargando' : anySeeding ? 'sembrando' : 'sin_descarga';
    return { ...group, progress, groupState };
  });
}

function groupStatus(group) {
  const map = {
    descargando: ['Descargando', 'info'],
    sembrando: ['Sembrando', 'ok'],
    pausado: ['Pausado', 'warn'],
    error: ['Error', 'bad'],
    sin_descarga: ['Sin descarga', 'muted'],
  };
  const [label, tone] = map[group.groupState] || map.sin_descarga;
  return status(label, tone);
}

function torrentRow(item) {
  const stateLabel = qbitState(item.state);
  const selected = state.torrentSelection.has(item.info_hash);
  return `<div class="dl-torrent">
    <input type="checkbox" data-action="torrent-select" data-hash="${esc(item.info_hash)}" ${selected ? 'checked' : ''} aria-label="Seleccionar torrent">
    <div class="name">
      <strong title="${esc(item.name || '')}">${esc(item.name || item.title || '—')}</strong>
      <small>${esc(item.tracker || item.category || '')}${item.release_title && item.release_title !== item.name ? `, ${esc(item.release_title)}` : ''}</small>
    </div>
    <div class="progress-cell">${bar(item.progress, item.progress >= 1 ? 'green' : '')}<span>${pct(item.progress)}%</span></div>
    <div class="tiny muted nowrap speed">${fmtSpeed(item.dlspeed || 0)}, ${fmtInt(item.num_seeds)} seeds, ETA ${fmtEta(item.eta)}</div>
    <div class="actions">
      ${status(stateLabel.label, stateLabel.tone)}
      <button class="btn btn-sm btn-ghost" data-action="qbit" data-hash="${esc(item.info_hash)}" data-qaction="${String(item.state || '').toLowerCase().includes('paus') || String(item.state || '').toLowerCase().includes('stop') ? 'resume' : 'pause'}" aria-label="${String(item.state || '').toLowerCase().includes('paus') ? 'Reanudar' : 'Pausar'}">${icon(String(item.state || '').toLowerCase().includes('paus') ? 'play' : 'pause', 'sm')}</button>
      <button class="btn btn-sm btn-ghost" data-action="qbit-delete" data-hash="${esc(item.info_hash)}" aria-label="Borrar torrent">${icon('trash', 'sm')}</button>
    </div>
  </div>`;
}

export async function renderDownloads({ query, signal }) {
  const text = query.get('q') || '';
  const filter = query.get('state') || '';
  const [data, settings] = await Promise.all([
    api('/api/downloads?category=ondemand', { signal }),
    api('/api/ondemand/settings', { signal }).catch(() => ({ max_active: 4, min: 1, max: 10 })),
  ]);
  setHeader('Descargas', 'Operación');

  const allGroups = groupItems(data.items || []).filter((group) => {
    const haystack = `${group.title} ${group.items.map((item) => item.name || '').join(' ')} ${group.keyName || ''}`.toLowerCase();
    if (text && !haystack.includes(text.toLowerCase())) return false;
    if (filter && group.groupState !== filter) return false;
    return true;
  });
  const counts = {
    '': groupItems(data.items || []).length,
    descargando: groupItems(data.items || []).filter((group) => group.groupState === 'descargando').length,
    sembrando: groupItems(data.items || []).filter((group) => group.groupState === 'sembrando').length,
    pausado: groupItems(data.items || []).filter((group) => group.groupState === 'pausado').length,
    error: groupItems(data.items || []).filter((group) => group.groupState === 'error').length,
  };

  const groups = allGroups.map((group) => {
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
        <div class="side">${groupStatus(group)}${group.kind === 'movie' && group.media_id ? `<a class="btn btn-sm btn-ghost" href="#/title/${group.media_id}">Abrir</a>` : ''}</div>
      </summary>
      <div class="dl-torrents">${group.items.map(torrentRow).join('')}</div>
    </details>`;
  }).join('');

  $('#view').innerHTML = `
    ${data.qbit_error ? `<div class="notice section">${esc(data.qbit_error)}</div>` : ''}
    <div class="toolbar">
      <label class="field grow">
        <span>Buscar</span>
        <input data-input="downloads-q" value="${esc(text)}" placeholder="Título o release" autocomplete="off" aria-label="Buscar descargas">
      </label>
      <label class="field" style="width:160px">
        <span>Límite on-demand</span>
        <input type="number" name="max_active" min="${settings.min || 1}" max="${settings.max || 10}" value="${settings.max_active || 4}" aria-label="Descargas simultáneas">
      </label>
      <button class="btn" data-action="save-limit">Guardar</button>
    </div>

    <div class="chips section" style="margin-bottom:12px">
      ${[['', 'Todas'], ['descargando', 'Descargando'], ['sembrando', 'Sembrando'], ['pausado', 'Pausadas'], ['error', 'Error']].map(([value, label]) => `<button class="chip ${filter === value ? 'active' : ''}" data-action="downloads-filter" data-value="${value}">${label} <b>${counts[value] || 0}</b></button>`).join('')}
      <span class="tiny faint" style="margin-left:auto">${allGroups.length} grupos, ${fmtInt(data.items?.length || 0)} torrents</span>
    </div>

    ${groups || `<div class="panel pad">${empty('Sin descargas on-demand', '', `<a class="btn btn-sm btn-ghost" href="#/catalog">Abrir catálogo</a>`)}</div>`}
  `;
}
