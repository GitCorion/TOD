import { api, apiPost } from '../api.js';
import { esc, fmtBytes, fmtInt, pct } from '../format.js';
import { healthStatus } from '../labels.js';
import { parseRoute, route, updateQuery } from '../router.js';
import { state } from '../store.js';
import { $, badge, bar, closeModal, confirmModal, empty, icon, openModal, status, toast } from '../ui.js';

export function formatQualityStack(release) {
  const tier = [release.source_type ? release.source_type.toUpperCase() : '', release.quality || ''].filter(Boolean).join(' ');
  const video = [release.codec, release.hdr].filter(Boolean).join(' · ');
  const audio = [release.audio, release.audio_codec, release.audio_channels].filter(Boolean).join(' · ');
  return {
    main: tier || release.quality || '—',
    video,
    audio,
  };
}

export function renderRichReleaseRow(release, { showProgress = false, showTrackerLink = true } = {}) {
  const health = healthStatus(release.health);
  const q = formatQualityStack(release);
  const trackerHref = showTrackerLink && (release.info_url || release.source === 'prowlarr')
    ? `/api/releases/${release.id}/tracker`
    : '';
  const progress = release.torrent_progress !== null && release.torrent_progress !== undefined
    ? `<div class="progress-cell">${bar(release.torrent_progress)}<span>${pct(release.torrent_progress)}%</span></div>`
    : '<span class="faint">—</span>';
  const cf = Number(release.cf_score || 0);
  const cfClass = cf > 0 ? 'ok' : cf < 0 ? 'bad' : 'muted';
  const breakdown = Array.isArray(release.score_breakdown) && release.score_breakdown.length
    ? release.score_breakdown.map((entry) => { const [name, sc] = Array.isArray(entry) ? entry : [entry?.name || 'CF', entry?.score ?? 0]; return `${name}: ${sc > 0 ? '+' : ''}${sc}`; }).join(' | ')
    : 'Sin Custom Formats aplicados';
  const rejections = Array.isArray(release.rejections) ? release.rejections : [];
  const rejectedHtml = rejections.length
    ? `<div class="rel-rejections" title="${esc(rejections.join('\n'))}">${icon('alert', 'sm')} <span>${esc(rejections[0])}${rejections.length > 1 ? ` (+${rejections.length - 1})` : ''}</span></div>`
    : '';
  const metaExtra = [
    release.release_group ? `-${release.release_group}` : '',
    release.edition || '',
    release.proper_rank > 0 ? 'PROPER/REPACK' : '',
    release.is_pack ? 'PACK' : '',
  ].filter(Boolean).join(' · ');

  return `<tr class="${release.allowed === false ? 'row-rejected' : ''}">
    <td data-label="Release">
      <div class="cell-title">
        <strong title="${esc(release.title || '')}">${esc(release.title || '—')}</strong>
        <small>
          ${esc(release.tracker || '')}, #${release.id}
          <span class="badge">${release.privacy === 'public' ? 'público' : 'privado'}</span>
          ${metaExtra ? `<span class="tiny faint"> · ${esc(metaExtra)}</span>` : ''}
        </small>
        ${rejectedHtml}
      </div>
    </td>
    <td data-label="Calidad">
      <strong>${esc(q.main)}</strong>
      ${q.video ? `<div class="tiny faint">${esc(q.video)}</div>` : ''}
      ${q.audio ? `<div class="tiny faint">${esc(q.audio)}</div>` : ''}
    </td>
    <td data-label="Score" class="num">
      <span class="status ${cfClass}" title="${esc(breakdown)}">${cf > 0 ? `+${cf}` : cf}</span>
    </td>
    <td data-label="Tamaño" class="num">${fmtBytes(release.size_bytes)}</td>
    <td data-label="Seeds" class="num">${fmtInt(release.seeders)} / ${fmtInt(release.peers)}</td>
    <td data-label="Salud">${status(health.label, health.tone)}</td>
    ${showProgress ? `<td data-label="Progreso">${progress}</td>` : ''}
    <td class="actions" data-label="">
      ${trackerHref ? `<a class="btn btn-sm btn-ghost" href="${trackerHref}" target="_blank" rel="noopener noreferrer">${icon('external', 'sm')} Ficha</a>` : ''}
      <button class="btn btn-sm btn-ghost" data-action="release-target" data-release="${release.id}" title="Usar este release por defecto para el .strm">Usar por defecto</button>
      <button class="btn btn-sm btn-ghost" data-action="release-health" data-release="${release.id}">Verificar</button>
      <button class="btn btn-sm ${release.allowed === false ? 'btn-ghost' : 'btn-primary'}" data-action="release-prepare" data-release="${release.id}" title="${release.allowed === false ? 'Forzar descarga/preparación (ignorar rechazo de perfil)' : 'Preparar reproducción en qBittorrent'}">${release.allowed === false ? 'Forzar' : 'Preparar'}</button>
      <button class="btn btn-sm btn-ghost danger-action" data-action="release-blocklist" data-release="${release.id}" title="Añadir a Blocklist">${icon('trash', 'sm')}</button>
    </td>
  </tr>`;
}

export async function openMassEditModal() {
  const keys = Array.from(state.selection || []);
  if (!keys.length) return;
  const [profData, rfData, tagData] = await Promise.all([
    api('/api/profiles').catch(() => ({ items: [] })),
    api('/api/root-folders').catch(() => ({ items: [] })),
    api('/api/tags').catch(() => ({ items: [] })),
  ]);
  const profiles = profData.items || [];
  const folders = rfData.items || [];
  const tags = tagData.items || [];

  const body = `<form id="massEditForm" class="stack" style="gap:12px">
    <p class="tiny muted">Se aplicará a <strong>${fmtInt(keys.length)}</strong> elemento(s) seleccionado(s). Deja «No cambiar» en los campos que no quieras modificar.</p>
    <div class="form-grid">
      <label class="field"><span>Perfil de Calidad</span>
        <select name="quality_profile_id" class="input">
          <option value="__keep__">— No cambiar —</option>
          <option value="">Heredar por defecto</option>
          ${profiles.map((p) => `<option value="${p.id}">${esc(p.name)}</option>`).join('')}
        </select>
      </label>
      <label class="field"><span>Carpeta de biblioteca</span>
        <select name="root_folder_id" class="input">
          <option value="__keep__">— No cambiar —</option>
          <option value="">Por defecto del sistema</option>
          ${folders.map((f) => `<option value="${f.id}">${esc(f.name)} (${esc(f.path)})</option>`).join('')}
        </select>
      </label>
      <label class="field"><span>Monitorización</span>
        <select name="monitor_action" class="input">
          <option value="__keep__">— No cambiar —</option>
          <option value="monitored:1">Monitorizar</option>
          <option value="monitored:0">Dejar de monitorizar</option>
          <option value="preset:all">Series: Todos</option>
          <option value="preset:future">Series: Solo futuros</option>
          <option value="preset:missing">Series: Solo faltantes</option>
          <option value="preset:latest_season">Series: Última temporada</option>
          <option value="preset:none">Series: Ninguno</option>
        </select>
      </label>
      <label class="field"><span>Añadir etiqueta</span>
        <select name="add_tag_id" class="input">
          <option value="">— Ninguna —</option>
          ${tags.map((t) => `<option value="${t.id}">${esc(t.name)}</option>`).join('')}
        </select>
      </label>
      <label class="field"><span>Quitar etiqueta</span>
        <select name="remove_tag_id" class="input">
          <option value="">— Ninguna —</option>
          ${tags.map((t) => `<option value="${t.id}">${esc(t.name)}</option>`).join('')}
        </select>
      </label>
    </div>
    <label class="inline check"><input type="checkbox" name="reorganize" checked> Mover y reorganizar ficheros .strm si cambia la carpeta</label>
  </form>`;

  openModal(
    `Editor masivo (${fmtInt(keys.length)} seleccionados)`,
    body,
    `<button class="btn btn-ghost" data-action="modal-close">Cancelar</button>
     <button class="btn btn-primary" data-action="mass-edit-apply">Aplicar cambios</button>`,
    { wide: true },
  );
}

async function applyMassEdit() {
  const form = $('#massEditForm');
  if (!form) return;
  const keys = Array.from(state.selection || []);
  if (!keys.length) { closeModal(); return; }
  const payload = { keys, reorganize: form.querySelector('[name="reorganize"]').checked };
  const qp = form.querySelector('[name="quality_profile_id"]').value;
  if (qp !== '__keep__') payload.quality_profile_id = qp || null;
  const rf = form.querySelector('[name="root_folder_id"]').value;
  if (rf !== '__keep__') payload.root_folder_id = rf || null;
  const mon = form.querySelector('[name="monitor_action"]').value;
  if (mon.startsWith('monitored:')) payload.monitored = mon === 'monitored:1';
  else if (mon.startsWith('preset:')) payload.monitor_mode = mon.split(':')[1];
  const addTag = form.querySelector('[name="add_tag_id"]').value;
  if (addTag) payload.add_tag_ids = [Number(addTag)];
  const remTag = form.querySelector('[name="remove_tag_id"]').value;
  if (remTag) payload.remove_tag_ids = [Number(remTag)];

  const res = await apiPost('/api/ondemand/mass-edit', payload);
  closeModal();
  toast(`Actualizados: ${fmtInt(res.movies || 0)} pelis, ${fmtInt(res.series || 0)} series`, 'ok');
  await route({ preserveScroll: true });
}

export async function runSeasonManualSearch(tmdb, season, query = '') {
  openModal(
    `Búsqueda de Temporada ${season} (Packs y Episodios)`,
    `<form class="row" data-form="season-search" data-tmdb="${tmdb}" data-season="${season}" style="margin-bottom:12px;align-items:flex-end">
      <label class="field grow"><span>Consulta</span>
        <input name="query" class="input" value="${esc(query)}" placeholder="Nombre de la serie S01">
      </label>
      <button class="btn btn-primary" type="submit">Buscar</button>
    </form>
    <div class="row" style="gap:9px"><span class="spinner"></span><span class="muted">Buscando packs y episodios en Prowlarr/Jackett…</span></div>`,
    `<button class="btn btn-ghost" data-action="modal-close">Cerrar</button>`,
    { wide: true },
  );
  try {
    const data = await apiPost(`/api/series/${tmdb}/season/${season}/manual-search`, { query });
    const releases = data.releases || [];
    const rows = releases.map((r) => renderRichReleaseRow(r)).join('');
    const content = `<form class="row" data-form="season-search" data-tmdb="${tmdb}" data-season="${season}" style="margin-bottom:12px;align-items:flex-end">
        <label class="field grow"><span>Consulta</span>
          <input name="query" class="input" value="${esc(data.query || query)}">
        </label>
        <button class="btn btn-primary" type="submit">Buscar</button>
      </form>
      <div class="row between" style="margin-bottom:10px">
        <span class="tiny muted">${fmtInt(releases.length)} candidatos (Packs primero)</span>
        <span class="tiny faint">${esc(data.title || '')} · Temporada ${season}</span>
      </div>
      ${rows ? `<div class="table-wrap"><table class="table--stack">
        <thead><tr><th>Release</th><th>Calidad</th><th class="num">Score</th><th class="num">Tamaño</th><th class="num">Seeds</th><th>Salud</th><th></th></tr></thead>
        <tbody>${rows}</tbody>
      </table></div>` : empty('Sin candidatos', 'Prueba ajustando el texto de búsqueda.')}`;
    openModal(
      `Temporada ${season}: ${data.title || ''}`,
      content,
      `<button class="btn btn-ghost" data-action="modal-close">Cerrar</button>`,
      { wide: true },
    );
  } catch (err) {
    toast(err.message, 'error');
  }
}

export async function handleArrViewAction(action, element) {
  if (action === 'release-blocklist') {
    const rid = element.dataset.release;
    const ok = await confirmModal({
      title: 'Añadir a Blocklist',
      message: 'TOD dejará de usar y mostrar este release automáticamente.',
      confirm: 'Añadir a Blocklist',
      danger: true,
    });
    if (!ok) return true;
    await apiPost(`/api/releases/${rid}/blocklist`, { reason: 'manual' });
    toast('Release añadido a la Blocklist', 'ok');
    element.closest('tr')?.remove();
    return true;
  }
  if (action === 'mass-edit-open') {
    await openMassEditModal();
    return true;
  }
  if (action === 'mass-edit-apply') {
    await applyMassEdit();
    return true;
  }
  if (action === 'custom-filter-save') {
    const q = parseRoute().query;
    const active = {};
    for (const key of ['kind', 'publication', 'download', 'origin', 'incomplete', 'profile_id', 'quality', 'audio', 'privacy', 'tracker', 'tag', 'genre', 'root_folder', 'monitored', 'cutoff_unmet']) {
      if (q.get(key)) active[key] = q.get(key);
    }
    openModal(
      'Guardar Filtro Personalizado',
      `<form id="saveCfForm" class="stack" style="gap:10px">
        <label class="field"><span>Nombre del filtro</span>
          <input name="name" class="input" placeholder="ej. Series 1080p Castellano" required>
        </label>
        <p class="tiny faint">Filtros activos: ${esc(JSON.stringify(active))}</p>
      </form>`,
      `<button class="btn btn-ghost" data-action="modal-close">Cancelar</button>
       <button class="btn btn-primary" data-action="custom-filter-save-run">Guardar</button>`,
    );
    return true;
  }
  if (action === 'custom-filter-save-run') {
    const form = $('#saveCfForm');
    if (!form) return true;
    const name = form.querySelector('[name="name"]').value.trim();
    if (!name) return true;
    const q = parseRoute().query;
    const filters = {};
    for (const key of ['kind', 'publication', 'download', 'origin', 'incomplete', 'profile_id', 'quality', 'audio', 'privacy', 'tracker', 'tag', 'genre', 'root_folder', 'monitored', 'cutoff_unmet']) {
      if (q.get(key)) filters[key] = q.get(key);
    }
    await apiPost('/api/custom-filters', { name, target: 'catalog', filters, is_pinned: true });
    closeModal();
    toast('Filtro guardado', 'ok');
    await route({ preserveScroll: true });
    return true;
  }
  if (action === 'custom-filter-apply') {
    const raw = element.dataset.filters ? JSON.parse(element.dataset.filters) : {};
    updateQuery(raw);
    return true;
  }
  if (action === 'custom-filter-delete') {
    await apiPost(`/api/custom-filters/${element.dataset.id}`, { _delete: true });
    toast('Filtro eliminado', 'ok');
    await route({ preserveScroll: true });
    return true;
  }
  if (action === 'series-settings-save') {
    const tmdb = element.dataset.tmdb;
    const box = element.closest('.series-settings-bar');
    if (!box) return true;
    const payload = {
      quality_profile_id: box.querySelector('[data-ss="quality_profile_id"]')?.value || null,
      root_folder_id: box.querySelector('[data-ss="root_folder_id"]')?.value || null,
      series_type: box.querySelector('[data-ss="series_type"]')?.value || 'standard',
    };
    const tagSel = box.querySelector('[data-ss="tag_id"]')?.value;
    if (tagSel !== undefined && tagSel !== '__keep__') {
      payload.tag_ids = tagSel ? [Number(tagSel)] : [];
    }
    await apiPost(`/api/series/${tmdb}/settings`, payload);
    toast('Ajustes de serie guardados', 'ok');
    await route({ preserveScroll: true });
    return true;
  }
  if (action === 'title-settings-save') {
    const id = element.dataset.id;
    const box = element.closest('.title-settings-box');
    if (!box) return true;
    const payload = {
      quality_profile_id: box.querySelector('[data-ts="quality_profile_id"]')?.value || null,
      root_folder_id: box.querySelector('[data-ts="root_folder_id"]')?.value || null,
      minimum_availability: box.querySelector('[data-ts="minimum_availability"]')?.value || 'released',
    };
    const tagSel = box.querySelector('[data-ts="tag_id"]')?.value;
    if (tagSel !== undefined && tagSel !== '__keep__') {
      payload.tag_ids = tagSel ? [Number(tagSel)] : [];
    }
    await apiPost(`/api/titles/${id}/settings`, payload);
    toast('Ajustes de título guardados', 'ok');
    await route({ preserveScroll: true });
    return true;
  }
  if (action === 'blocklist-remove') {
    await apiPost(`/api/blocklist/${element.dataset.release}/delete`, {});
    toast('Fuente liberada de la blocklist', 'ok');
    await route({ preserveScroll: true });
    return true;
  }
  if (action === 'wanted-upgrade-cutoff') {
    element.disabled = true;
    try {
      const res = await apiPost('/api/monitor/upgrade-cutoff', {});
      toast(`Selección de releases actualizada (${fmtInt(res.upgraded || 0)} mejoras aplicadas sin descargas)`, 'ok');
      await route({ preserveScroll: true });
    } finally {
      element.disabled = false;
    }
    return true;
  }
  return false;
}
