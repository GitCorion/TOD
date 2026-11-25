import { api, apiPost } from '../api.js';
import { esc, fmtBytes, fmtDate, fmtInt } from '../format.js';
import { route } from '../router.js';
import { $, badge, empty, icon, setHeader, status, toast } from '../ui.js';

export async function renderBlocklist({ signal }) {
  setHeader('Blocklist');
  const data = await api('/api/blocklist', { signal });
  const items = data.items || [];

  const rows = items.map((item) => {
    const href = item.kind === 'series' && item.tmdb
      ? `#/series/${item.tmdb}`
      : item.asset_id ? `#/title/${item.asset_id}` : '#/catalog';
    const epTag = item.kind === 'series' && item.season
      ? ` S${String(item.season).padStart(2, '0')}E${String(item.episode || 0).padStart(2, '0')}`
      : '';
    return `<tr>
      <td data-label="Título">
        <a href="${href}"><strong>${esc(item.media_title || `#${item.asset_id || item.release_id}`)}</strong>${esc(epTag)}</a>
        <div class="tiny faint">${esc(item.release_title || 'Release eliminado')}</div>
      </td>
      <td data-label="Tracker">
        <span>${esc(item.tracker || '—')}</span>
        ${item.privacy ? `<div>${badge(item.privacy === 'public' ? 'público' : 'privado')}</div>` : ''}
      </td>
      <td data-label="Calidad">
        <span>${esc(item.quality || '—')}</span>
        <div class="tiny faint">${esc(item.audio || '')}${item.size_bytes ? ` · ${fmtBytes(item.size_bytes)}` : ''}</div>
      </td>
      <td data-label="Motivo">${status(item.reason || 'bloqueado', 'bad')}</td>
      <td data-label="Expira" class="tiny faint nowrap">${item.until ? fmtDate(item.until) : '—'}</td>
      <td class="actions" data-label="">
        <button class="btn btn-sm btn-ghost" data-action="blocklist-remove" data-release="${item.release_id}">
          ${icon('refresh', 'sm')} Liberar
        </button>
      </td>
    </tr>`;
  }).join('');

  $('#view').innerHTML = `
    <div class="panel flush section">
      <div class="panel-head">
        <h2>Fuentes vetadas (Blocklist)</h2>
        <span class="right tiny faint">${fmtInt(items.length)} bloqueadas</span>
      </div>
      ${rows ? `<div class="table-wrap"><table class="table--stack">
        <thead><tr><th>Título / Versión</th><th>Tracker</th><th>Calidad</th><th>Motivo</th><th>Expira</th><th></th></tr></thead>
        <tbody>${rows}</tbody>
      </table></div>` : `<div class="pad">${empty('Sin fuentes en blocklist', 'Las fuentes rechazadas automáticamente por fallos o vetadas manualmente aparecerán aquí.')}</div>`}
    </div>
  `;
}

export async function removeBlocklistItem(releaseId) {
  await apiPost(`/api/blocklist/${releaseId}/delete`, {});
  toast('Fuente liberada de la blocklist', 'ok');
  await route({ preserveScroll: true });
}
