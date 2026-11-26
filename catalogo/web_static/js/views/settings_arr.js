import { api, apiPost } from '../api.js';
import { esc, fmtInt } from '../format.js';
import { route } from '../router.js';
import { invalidateMetaCache } from '../store.js';
import { $, badge, closeModal, confirmModal, empty, icon, openModal, status, toast } from '../ui.js';

const PRESET_LABELS = {
  all: 'Todos los episodios',
  future: 'Solo futuros',
  missing: 'Solo faltantes',
  first_season: 'Primera temporada',
  latest_season: 'Última temporada',
  pilot: 'Solo piloto (S01E01)',
  none: 'Ninguno',
};

const LIST_KIND_LABELS = {
  tmdb_collection: 'Colección TMDB',
  tmdb_list: 'Lista TMDB',
  tmdb_popular: 'Populares TMDB',
  tmdb_discover: 'Discover TMDB',
  url: 'URL JSON',
};

let arrCache = {
  profiles: [],
  customFormats: [],
  restrictions: [],
  qualities: [],
  naming: {},
  examples: {},
  rootFolders: [],
  tags: [],
  importLists: [],
};

export async function fetchArrSettingsData(signal) {
  const [profData, namData, rfData, tagData, listData] = await Promise.all([
    api('/api/profiles', { signal }).catch(() => ({ items: [], custom_formats: [], restrictions: [], qualities: [] })),
    api('/api/naming', { signal }).catch(() => ({ settings: {}, examples: {} })),
    api('/api/root-folders', { signal }).catch(() => ({ items: [] })),
    api('/api/tags', { signal }).catch(() => ({ items: [] })),
    api('/api/import-lists', { signal }).catch(() => ({ items: [] })),
  ]);
  arrCache = {
    profiles: profData.items || [],
    customFormats: profData.custom_formats || [],
    restrictions: profData.restrictions || [],
    qualities: profData.qualities || [],
    naming: namData.settings || {},
    examples: namData.examples || {},
    rootFolders: rfData.items || [],
    tags: tagData.items || [],
    importLists: listData.items || [],
  };
  return arrCache;
}

export function renderArrSettingsSections(data) {
  const {
    profiles,
    customFormats,
    restrictions,
    naming,
    examples,
    rootFolders,
    tags,
    importLists,
  } = data;

  const profileRows = profiles.map((p) => {
    const allowed = (p.allowed_qualities || []).slice(0, 5).join(', ');
    const more = (p.allowed_qualities || []).length > 5 ? ` +${p.allowed_qualities.length - 5}` : '';
    const limits = [
      p.max_size_gb > 0 ? `máx ${p.max_size_gb} GB` : '',
      p.max_mb_per_min > 0 ? `máx ${p.max_mb_per_min} MB/min` : '',
      p.block_cam ? 'sin CAM' : '',
      p.block_disc_images ? 'sin ISO/BDMV' : '',
    ].filter(Boolean).join(' · ');
    return `<tr>
      <td data-label="Perfil">
        <strong>${esc(p.name)}</strong> ${p.is_default ? badge('por defecto', 'ok') : ''}
        <div class="tiny faint">${esc(limits || 'Sin límites extra')}</div>
      </td>
      <td data-label="Objetivo">${p.cutoff_quality ? badge(p.cutoff_quality, 'mono') : '<span class="faint">—</span>'}</td>
      <td data-label="Idioma" class="tiny">${esc(p.language_preference === 'inherit' || !p.language_preference ? 'Heredar ajuste global' : p.language_preference)}</td>
      <td data-label="Calidades" class="tiny faint">${esc(allowed + more)}</td>
      <td class="actions" data-label="">
        <button class="btn btn-sm btn-ghost" data-action="arr-profile-edit" data-id="${p.id}">Editar</button>
        ${p.is_default ? '' : `<button class="btn btn-sm btn-ghost danger-action" data-action="arr-profile-delete" data-id="${p.id}">Borrar</button>`}
      </td>
    </tr>`;
  }).join('');

  const cfRows = customFormats.map((cf) => {
    const specs = (cf.specs || []).map((s) => `${s.negate ? 'NO ' : ''}${s.field}=${s.value}`).join(', ');
    return `<tr>
      <td data-label="Formato"><strong>${esc(cf.name)}</strong></td>
      <td data-label="Condiciones" class="tiny mono">${esc(specs || '—')}</td>
      <td data-label="Estado">${cf.enabled ? status('Activo', 'ok') : status('Desactivado', 'muted')}</td>
      <td class="actions" data-label="">
        <button class="btn btn-sm btn-ghost" data-action="arr-cf-edit" data-id="${cf.id}">Editar</button>
        <button class="btn btn-sm btn-ghost danger-action" data-action="arr-cf-delete" data-id="${cf.id}">Borrar</button>
      </td>
    </tr>`;
  }).join('');

  const restRows = restrictions.map((r) => `<tr>
    <td data-label="Regla"><strong>${esc(r.name)}</strong></td>
    <td data-label="Debe contener" class="tiny mono">${esc(r.must_contain || '—')}</td>
    <td data-label="No debe contener" class="tiny mono">${esc(r.must_not_contain || '—')}</td>
    <td class="actions" data-label="">
      <button class="btn btn-sm btn-ghost danger-action" data-action="arr-rest-delete" data-id="${r.id}">Borrar</button>
    </td>
  </tr>`).join('');

  const rfRows = rootFolders.map((rf) => `<tr>
    <td data-label="Nombre"><strong>${esc(rf.name)}</strong> ${rf.is_default ? badge('por defecto', 'ok') : ''}</td>
    <td data-label="Tipo">${badge(rf.kind, 'mono')}</td>
    <td data-label="Ruta" class="mono tiny">${esc(rf.path)}</td>
    <td class="actions" data-label="">
      ${rf.is_default ? '' : `<button class="btn btn-sm btn-ghost danger-action" data-action="arr-rf-delete" data-id="${rf.id}">Borrar</button>`}
    </td>
  </tr>`).join('');

  const tagChips = tags.map((t) => `<span class="tag-pill" style="--tag-color:${esc(t.color || '#6b7280')}">
    <i aria-hidden="true"></i>
    <span>${esc(t.name)}</span>
    <small>${fmtInt(t.media_count || 0)}</small>
    <button class="tag-del" data-action="arr-tag-delete" data-id="${t.id}" aria-label="Borrar etiqueta ${esc(t.name)}">${icon('close', 'sm')}</button>
  </span>`).join('');

  const listRows = importLists.map((il) => `<tr>
    <td data-label="Lista">
      <strong>${esc(il.name)}</strong>
      <div class="tiny faint">${esc(LIST_KIND_LABELS[il.kind] || il.kind)}: <span class="mono">${esc(il.source_ref)}</span></div>
    </td>
    <td data-label="Tipo">${badge(il.media_kind === 'series' ? 'Serie' : 'Película')}</td>
    <td data-label="Destino" class="tiny">
      ${esc(il.profile_name || 'Por defecto')}
      ${il.tag_name ? ` · ${badge(il.tag_name)}` : ''}
    </td>
    <td data-label="Estado" class="tiny faint">${esc(il.last_status || 'Sin ejecutar')}</td>
    <td class="actions" data-label="">
      <button class="btn btn-sm btn-ghost" data-action="arr-list-run" data-id="${il.id}">${icon('refresh', 'sm')} Ejecutar</button>
      <button class="btn btn-sm btn-ghost danger-action" data-action="arr-list-delete" data-id="${il.id}">Borrar</button>
    </td>
  </tr>`).join('');

  return `
    <div class="panel section" id="settings-profiles">
      <div class="panel-head">
        <h2>Perfiles de calidad</h2>
        <div class="right row" style="gap:8px">
          <button class="btn btn-sm btn-primary" data-action="arr-profile-new">${icon('plus', 'sm')} Nuevo perfil</button>
        </div>
      </div>
      <div class="panel-body" style="padding:0">
        <div class="table-wrap"><table class="table--stack">
          <thead><tr><th>Perfil</th><th>Objetivo</th><th>Idioma</th><th>Calidades permitidas</th><th></th></tr></thead>
          <tbody>${profileRows || '<tr><td colspan="5" class="tiny faint">Sin perfiles</td></tr>'}</tbody>
        </table></div>
      </div>
    </div>

    <div class="panel section" id="settings-formats">
      <div class="panel-head">
        <h2>Custom Formats y Restricciones</h2>
        <div class="right row" style="gap:8px">
          <button class="btn btn-sm btn-ghost" data-action="arr-cf-new">${icon('plus', 'sm')} Nuevo Formato</button>
          <button class="btn btn-sm btn-ghost" data-action="arr-rest-new">${icon('plus', 'sm')} Nueva Restricción</button>
        </div>
      </div>
      <div class="panel-body">
        <h3 class="tiny muted" style="margin:0 0 8px;text-transform:uppercase;letter-spacing:.04em">Custom Formats</h3>
        <div class="table-wrap section" style="margin-top:0"><table class="table--stack">
          <thead><tr><th>Nombre</th><th>Condiciones</th><th>Estado</th><th></th></tr></thead>
          <tbody>${cfRows || '<tr><td colspan="4" class="tiny faint">Sin Custom Formats</td></tr>'}</tbody>
        </table></div>
        <h3 class="tiny muted" style="margin:16px 0 8px;text-transform:uppercase;letter-spacing:.04em">Restricciones (Must Contain / Must Not Contain)</h3>
        <div class="table-wrap"><table class="table--stack">
          <thead><tr><th>Regla</th><th>Debe contener</th><th>No debe contener</th><th></th></tr></thead>
          <tbody>${restRows || '<tr><td colspan="4" class="tiny faint">Sin restricciones personalizadas</td></tr>'}</tbody>
        </table></div>
      </div>
    </div>

    <div class="panel section" id="settings-media">
      <div class="panel-head">
        <h2>Gestión de medios (.strm y .nfo)</h2>
        <div class="right row" style="gap:8px">
          <button class="btn btn-sm btn-ghost" data-action="arr-rf-new">${icon('plus', 'sm')} Nueva carpeta raíz</button>
          <button class="btn btn-sm btn-ghost" data-action="arr-reorg-preview">${icon('refresh', 'sm')} Vista previa de renombrado (.strm)</button>
        </div>
      </div>
      <div class="panel-body">
        <h3 class="tiny muted" style="margin:0 0 8px;text-transform:uppercase;letter-spacing:.04em">Carpetas de biblioteca</h3>
        <div class="table-wrap section" style="margin-top:0"><table class="table--stack">
          <thead><tr><th>Nombre</th><th>Tipo</th><th>Ruta</th><th></th></tr></thead>
          <tbody>${rfRows || '<tr><td colspan="4" class="tiny faint">Sin carpetas raíz</td></tr>'}</tbody>
        </table></div>

        <h3 class="tiny muted" style="margin:16px 0 8px;text-transform:uppercase;letter-spacing:.04em">Plantillas de Nombrado (.strm y .nfo)</h3>
        <div class="form-grid" id="namingForm">
          <label class="field"><span>Carpeta de película</span>
            <input class="input mono" data-naming="movie_folder" value="${esc(naming.movie_folder || '')}">
          </label>
          <label class="field"><span>Fichero de película</span>
            <input class="input mono" data-naming="movie_file" value="${esc(naming.movie_file || '')}">
          </label>
          <label class="field"><span>Carpeta de serie</span>
            <input class="input mono" data-naming="series_folder" value="${esc(naming.series_folder || '')}">
          </label>
          <label class="field"><span>Carpeta de temporada</span>
            <input class="input mono" data-naming="season_folder" value="${esc(naming.season_folder || '')}">
          </label>
          <label class="field"><span>Fichero de episodio</span>
            <input class="input mono" data-naming="episode_file" value="${esc(naming.episode_file || '')}">
          </label>
          <label class="field"><span>Carpeta Anime (si se separa)</span>
            <input class="input mono" data-naming="anime_series_folder" value="${esc(naming.anime_series_folder || 'anime')}">
          </label>
          <label class="inline check" style="align-self:end;padding-bottom:6px">
            <input type="checkbox" data-naming="use_season_folders" ${naming.use_season_folders !== false ? 'checked' : ''}>
            Usar carpetas de temporada
          </label>
          <label class="inline check" style="align-self:end;padding-bottom:6px">
            <input type="checkbox" data-naming="separate_anime" ${naming.separate_anime ? 'checked' : ''}>
            Separar Anime en carpeta propia
          </label>
          <label class="inline check" style="align-self:end;padding-bottom:6px">
            <input type="checkbox" data-naming="rich_nfo" ${naming.rich_nfo !== false ? 'checked' : ''}>
            Generar .nfo enriquecidos (IMDb, géneros, sinopsis)
          </label>
          <label class="field"><span>&nbsp;</span>
            <button class="btn btn-primary" data-action="arr-naming-save">${icon('check', 'sm')} Guardar nombrado</button>
          </label>
        </div>
        <div class="naming-preview" id="namingPreview">
          <div><span class="tiny faint">Película:</span> <code class="tiny mono">${esc(examples.movie || '')}</code></div>
          <div><span class="tiny faint">Serie:</span> <code class="tiny mono">${esc(examples.series || '')}</code></div>
          <div><span class="tiny faint">Anime:</span> <code class="tiny mono">${esc(examples.anime || '')}</code></div>
        </div>
      </div>
    </div>

    <div class="panel section" id="settings-lists">
      <div class="panel-head">
        <h2>Etiquetas y Listas de importación</h2>
        <div class="right row" style="gap:8px">
          <button class="btn btn-sm btn-ghost" data-action="arr-list-new">${icon('plus', 'sm')} Nueva lista de importación</button>
        </div>
      </div>
      <div class="panel-body">
        <div class="row" style="gap:8px;margin-bottom:12px;flex-wrap:wrap">
          <input id="newTagName" class="input" style="max-width:200px" placeholder="Nueva etiqueta (ej. anime)" autocomplete="off">
          <input id="newTagColor" class="input" type="color" value="#eab64b" style="width:46px;padding:2px">
          <button class="btn btn-sm btn-primary" data-action="arr-tag-add">${icon('plus', 'sm')} Añadir etiqueta</button>
        </div>
        <div class="tags-wrap" style="margin-bottom:16px">${tagChips || '<span class="tiny faint">Sin etiquetas todavía.</span>'}</div>

        <h3 class="tiny muted" style="margin:12px 0 8px;text-transform:uppercase;letter-spacing:.04em">Listas de importación (auto-añadido)</h3>
        <div class="table-wrap"><table class="table--stack">
          <thead><tr><th>Nombre / Origen</th><th>Contenido</th><th>Perfil / Etiqueta</th><th>Última ejecución</th><th></th></tr></thead>
          <tbody>${listRows || '<tr><td colspan="5" class="tiny faint">Sin listas configuradas.</td></tr>'}</tbody>
        </table></div>
      </div>
    </div>
  `;
}

function openProfileModal(existing = null) {
  const p = existing || {
    name: '',
    is_default: false,
    quality_order: [...arrCache.qualities],
    allowed_qualities: ['1080p', '2160p', '720p'],
    cutoff_quality: '1080p',
    language_preference: 'inherit',
    upgrade_allowed: true,
    prefer_smaller: true,
    max_size_gb: 0,
    max_mb_per_min: 0,
    block_cam: true,
    block_disc_images: true,
    min_score: -1000,
    cutoff_score: '',
    radarr_profile_id: '',
    sonarr_profile_id: '',
    format_scores: {},
  };
  const allQualities = arrCache.qualities.length
    ? arrCache.qualities
    : ['2160p', '1080p', '720p', '480p'];
  const order = (p.quality_order && p.quality_order.length ? p.quality_order : allQualities);
  const allowedSet = new Set(p.allowed_qualities || []);

  const cfHtml = arrCache.customFormats.map((cf) => {
    const val = p.format_scores?.[cf.id] ?? p.format_scores?.[String(cf.id)] ?? 0;
    return `<label class="field"><span>${esc(cf.name)}</span>
      <input class="input" type="number" data-cf-score="${cf.id}" value="${esc(val)}">
    </label>`;
  }).join('');

  const body = `<form id="arrProfileForm" class="stack" style="gap:12px">
    <div class="form-grid">
      <label class="field"><span>Nombre</span>
        <input name="name" class="input" value="${esc(p.name)}" required>
      </label>
      <label class="field"><span>Calidad objetivo</span>
        <select name="cutoff_quality" class="input">
          ${allQualities.map((q) => `<option value="${esc(q)}" ${p.cutoff_quality === q ? 'selected' : ''}>${esc(q)}</option>`).join('')}
        </select>
      </label>
      <label class="field"><span>Idioma</span>
        <select name="language_preference" class="input">
          <option value="inherit" ${p.language_preference === 'inherit' ? 'selected' : ''}>Heredar ajuste global</option>
          <option value="castellano_first" ${p.language_preference === 'castellano_first' ? 'selected' : ''}>Castellano primero (sin latino)</option>
          <option value="only_castellano" ${p.language_preference === 'only_castellano' ? 'selected' : ''}>Solo Castellano</option>
          <option value="vose_first" ${p.language_preference === 'vose_first' ? 'selected' : ''}>VOSE / V.O. primero</option>
          <option value="any" ${p.language_preference === 'any' ? 'selected' : ''}>Cualquier idioma</option>
        </select>
      </label>
      <label class="field"><span>Peso máximo (GB, 0=sin límite)</span>
        <input name="max_size_gb" class="input" type="number" min="0" step="0.5" value="${esc(p.max_size_gb ?? 0)}">
      </label>
      <label class="field"><span>Bitrate máx. streaming (MB/min, 0=sin límite)</span>
        <input name="max_mb_per_min" class="input" type="number" min="0" step="5" value="${esc(p.max_mb_per_min ?? 0)}">
      </label>
      <label class="field"><span>Puntuación mínima aceptable</span>
        <input name="min_score" class="input" type="number" value="${esc(p.min_score ?? -1000)}">
      </label>
      <label class="field"><span>ID Perfil Radarr (puente)</span>
        <input name="radarr_profile_id" class="input" type="number" value="${esc(p.radarr_profile_id ?? '')}" placeholder="ej. 4">
      </label>
      <label class="field"><span>ID Perfil Sonarr (puente)</span>
        <input name="sonarr_profile_id" class="input" type="number" value="${esc(p.sonarr_profile_id ?? '')}" placeholder="ej. 4">
      </label>
    </div>
    <div class="row" style="gap:14px;flex-wrap:wrap">
      <label class="inline check"><input type="checkbox" name="is_default" ${p.is_default ? 'checked' : ''}> Perfil por defecto</label>
      <label class="inline check"><input type="checkbox" name="upgrade_allowed" ${p.upgrade_allowed !== false ? 'checked' : ''}> Permitir mejoras automáticas</label>
      <label class="inline check"><input type="checkbox" name="prefer_smaller" ${p.prefer_smaller !== false ? 'checked' : ''}> Menor tamaño primero</label>
      <label class="inline check"><input type="checkbox" name="block_cam" ${p.block_cam !== false ? 'checked' : ''}> Bloquear CAM/TS</label>
      <label class="inline check"><input type="checkbox" name="block_disc_images" ${p.block_disc_images !== false ? 'checked' : ''}> Bloquear ISO/BDMV</label>
    </div>
    <div>
      <span class="tiny muted">Calidades permitidas y orden de preferencia (escribe separadas por coma si quieres reordenar):</span>
      <input name="quality_order_text" class="input mono" value="${esc(order.join(', '))}" style="margin-top:4px">
      <div class="row" style="gap:10px;margin-top:6px;flex-wrap:wrap">
        ${allQualities.map((q) => `<label class="inline check"><input type="checkbox" data-q-allow="${esc(q)}" ${allowedSet.has(q) ? 'checked' : ''}> ${esc(q)}</label>`).join('')}
      </div>
    </div>
    ${cfHtml ? `<div><span class="tiny muted">Puntuación de Custom Formats (+ preferido / - penalizado):</span><div class="form-grid" style="margin-top:6px">${cfHtml}</div></div>` : ''}
  </form>`;

  openModal(
    p.id ? `Editar perfil: ${p.name}` : 'Nuevo Perfil de Calidad',
    body,
    `<button class="btn btn-ghost" data-action="modal-close">Cancelar</button>
     <button class="btn btn-primary" data-action="arr-profile-save" data-id="${p.id || ''}">Guardar perfil</button>`,
    { wide: true },
  );
}

async function saveProfileFromModal(id) {
  const form = $('#arrProfileForm');
  if (!form) return;
  const orderText = form.querySelector('[name="quality_order_text"]')?.value || '';
  const quality_order = orderText.split(',').map((s) => s.trim()).filter(Boolean);
  const allowed_qualities = Array.from(form.querySelectorAll('[data-q-allow]:checked')).map((el) => el.dataset.qAllow);
  const format_scores = {};
  form.querySelectorAll('[data-cf-score]').forEach((el) => {
    format_scores[el.dataset.cfScore] = Number(el.value || 0);
  });
  const payload = {
    name: form.querySelector('[name="name"]').value.trim(),
    cutoff_quality: form.querySelector('[name="cutoff_quality"]').value,
    language_preference: form.querySelector('[name="language_preference"]').value,
    max_size_gb: Number(form.querySelector('[name="max_size_gb"]').value || 0),
    max_mb_per_min: Number(form.querySelector('[name="max_mb_per_min"]').value || 0),
    min_score: Number(form.querySelector('[name="min_score"]').value || -1000),
    radarr_profile_id: form.querySelector('[name="radarr_profile_id"]').value || null,
    sonarr_profile_id: form.querySelector('[name="sonarr_profile_id"]').value || null,
    is_default: form.querySelector('[name="is_default"]').checked,
    upgrade_allowed: form.querySelector('[name="upgrade_allowed"]').checked,
    prefer_smaller: form.querySelector('[name="prefer_smaller"]').checked,
    block_cam: form.querySelector('[name="block_cam"]').checked,
    block_disc_images: form.querySelector('[name="block_disc_images"]').checked,
    quality_order,
    allowed_qualities,
    format_scores,
  };
  const endpoint = id ? `/api/profiles/${id}` : '/api/profiles';
  await apiPost(endpoint, payload);
  closeModal();
  invalidateMetaCache(); toast('Perfil guardado', 'ok');
  await route({ preserveScroll: true });
}

function openCustomFormatModal(existing = null) {
  const cf = existing || { name: '', enabled: true, specs: [{ field: 'video_codec', value: 'HEVC', negate: false }] };
  const s = cf.specs?.[0] || { field: 'video_codec', value: '', negate: false };
  const body = `<form id="arrCfForm" class="stack" style="gap:10px">
    <label class="field"><span>Nombre</span><input name="name" class="input" value="${esc(cf.name)}" required></label>
    <div class="form-grid">
      <label class="field"><span>Campo a evaluar</span>
        <select name="field" class="input">
          ${['source_type', 'video_codec', 'hdr', 'audio', 'audio_codec', 'privacy', 'title_regex', 'release_group'].map((f) => `<option value="${f}" ${s.field === f ? 'selected' : ''}>${f}</option>`).join('')}
        </select>
      </label>
      <label class="field"><span>Valor / Regex</span><input name="value" class="input mono" value="${esc(s.value || '')}" placeholder="ej. HEVC o x265|HEVC"></label>
    </div>
    <div class="row" style="gap:14px">
      <label class="inline check"><input type="checkbox" name="negate" ${s.negate ? 'checked' : ''}> Negar condición (NO coincide)</label>
      <label class="inline check"><input type="checkbox" name="enabled" ${cf.enabled !== false ? 'checked' : ''}> Activo</label>
    </div>
  </form>`;
  openModal(
    cf.id ? `Editar formato: ${cf.name}` : 'Nuevo Custom Format',
    body,
    `<button class="btn btn-ghost" data-action="modal-close">Cancelar</button>
     <button class="btn btn-primary" data-action="arr-cf-save" data-id="${cf.id || ''}">Guardar formato</button>`,
  );
}

function openRestrictionModal() {
  const body = `<form id="arrRestForm" class="stack" style="gap:10px">
    <label class="field"><span>Nombre de la regla</span><input name="name" class="input" placeholder="ej. Bloquear Telesync" required></label>
    <label class="field"><span>Debe contener (regex/palabras, opcional)</span><input name="must_contain" class="input mono" placeholder="ej. (castellano|esp)"></label>
    <label class="field"><span>NO debe contener (regex/palabras, opcional)</span><input name="must_not_contain" class="input mono" placeholder="ej. (CAM|TS|HDCAM|SCREENER)"></label>
  </form>`;
  openModal(
    'Nueva Restricción de Release',
    body,
    `<button class="btn btn-ghost" data-action="modal-close">Cancelar</button>
     <button class="btn btn-primary" data-action="arr-rest-save">Guardar restricción</button>`,
  );
}

function openRootFolderModal() {
  const body = `<form id="arrRfForm" class="stack" style="gap:10px">
    <label class="field"><span>Nombre</span><input name="name" class="input" placeholder="ej. Anime" required></label>
    <label class="field"><span>Tipo</span>
      <select name="kind" class="input">
        <option value="any">Cualquiera</option>
        <option value="movie">Películas</option>
        <option value="series">Series</option>
        <option value="anime">Anime</option>
      </select>
    </label>
    <label class="field"><span>Ruta absoluta</span><input name="path" class="input mono" placeholder="/mnt/catalogo/anime" required></label>
    <label class="inline check"><input type="checkbox" name="is_default"> Por defecto para este tipo</label>
  </form>`;
  openModal(
    'Nueva carpeta de biblioteca',
    body,
    `<button class="btn btn-ghost" data-action="modal-close">Cancelar</button>
     <button class="btn btn-primary" data-action="arr-rf-save">Añadir carpeta</button>`,
  );
}

function openImportListModal() {
  const body = `<form id="arrListForm" class="stack" style="gap:10px">
    <div class="form-grid">
      <label class="field"><span>Nombre</span><input name="name" class="input" placeholder="ej. Saga Star Wars" required></label>
      <label class="field"><span>Origen</span>
        <select name="kind" class="input">
          <option value="tmdb_collection">Colección TMDB (ID)</option>
          <option value="tmdb_list">Lista TMDB (ID)</option>
          <option value="tmdb_popular">TMDB Populares (páginas 1-3)</option>
          <option value="tmdb_discover">TMDB Discover (params URL)</option>
          <option value="url">URL JSON personalizada</option>
        </select>
      </label>
      <label class="field"><span>Referencia / ID / URL</span><input name="source_ref" class="input mono" placeholder="ej. 10 o https://..." required></label>
      <label class="field"><span>Contenido</span>
        <select name="media_kind" class="input">
          <option value="movie">Películas</option>
          <option value="series">Series</option>
        </select>
      </label>
      <label class="field"><span>Perfil asignado</span>
        <select name="quality_profile_id" class="input">
          <option value="">Por defecto</option>
          ${arrCache.profiles.map((p) => `<option value="${p.id}">${esc(p.name)}</option>`).join('')}
        </select>
      </label>
      <label class="field"><span>Carpeta raíz</span>
        <select name="root_folder_id" class="input">
          <option value="">Por defecto</option>
          ${arrCache.rootFolders.map((r) => `<option value="${r.id}">${esc(r.name)}</option>`).join('')}
        </select>
      </label>
      <label class="field"><span>Etiqueta</span>
        <select name="tag_id" class="input">
          <option value="">Ninguna</option>
          ${arrCache.tags.map((t) => `<option value="${t.id}">${esc(t.name)}</option>`).join('')}
        </select>
      </label>
      <label class="field"><span>Seguimiento</span>
        <select name="monitor_mode" class="input">
          ${Object.entries(PRESET_LABELS).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join('')}
        </select>
      </label>
    </div>
  </form>`;
  openModal(
    'Nueva Import List',
    body,
    `<button class="btn btn-ghost" data-action="modal-close">Cancelar</button>
     <button class="btn btn-primary" data-action="arr-list-save">Crear lista</button>`,
    { wide: true },
  );
}

export async function handleSettingsArrAction(action, element) {
  if (action === 'arr-profile-new') { openProfileModal(null); return true; }
  if (action === 'arr-profile-edit') {
    const p = arrCache.profiles.find((x) => Number(x.id) === Number(element.dataset.id));
    openProfileModal(p || null);
    return true;
  }
  if (action === 'arr-profile-save') { await saveProfileFromModal(element.dataset.id || null); return true; }
  if (action === 'arr-profile-delete') {
    const ok = await confirmModal({ title: 'Borrar perfil', message: '¿Seguro que deseas borrar este perfil?', confirm: 'Borrar', danger: true });
    if (!ok) return true;
    await apiPost(`/api/profiles/${element.dataset.id}`, { _delete: true });
    invalidateMetaCache(); toast('Perfil eliminado', 'ok');
    await route({ preserveScroll: true });
    return true;
  }
  if (action === 'arr-cf-new') { openCustomFormatModal(null); return true; }
  if (action === 'arr-cf-edit') {
    const cf = arrCache.customFormats.find((x) => Number(x.id) === Number(element.dataset.id));
    openCustomFormatModal(cf || null);
    return true;
  }
  if (action === 'arr-cf-save') {
    const form = $('#arrCfForm');
    if (!form) return true;
    const id = element.dataset.id;
    await apiPost(id ? `/api/custom-formats/${id}` : '/api/custom-formats', {
      name: form.querySelector('[name="name"]').value.trim(),
      enabled: form.querySelector('[name="enabled"]').checked,
      specs: [{
        field: form.querySelector('[name="field"]').value,
        value: form.querySelector('[name="value"]').value.trim(),
        negate: form.querySelector('[name="negate"]').checked,
      }],
    });
    closeModal();
    toast('Custom Format guardado', 'ok');
    await route({ preserveScroll: true });
    return true;
  }
  if (action === 'arr-cf-delete') {
    await apiPost(`/api/custom-formats/${element.dataset.id}`, { _delete: true });
    toast('Custom Format eliminado', 'ok');
    await route({ preserveScroll: true });
    return true;
  }
  if (action === 'arr-rest-new') { openRestrictionModal(); return true; }
  if (action === 'arr-rest-save') {
    const form = $('#arrRestForm');
    if (!form) return true;
    await apiPost('/api/restrictions', {
      name: form.querySelector('[name="name"]').value.trim(),
      must_contain: form.querySelector('[name="must_contain"]').value.trim(),
      must_not_contain: form.querySelector('[name="must_not_contain"]').value.trim(),
    });
    closeModal();
    toast('Restricción guardada', 'ok');
    await route({ preserveScroll: true });
    return true;
  }
  if (action === 'arr-rest-delete') {
    await apiPost(`/api/restrictions/${element.dataset.id}`, { _delete: true });
    toast('Restricción eliminada', 'ok');
    await route({ preserveScroll: true });
    return true;
  }
  if (action === 'arr-rf-new') { openRootFolderModal(); return true; }
  if (action === 'arr-rf-save') {
    const form = $('#arrRfForm');
    if (!form) return true;
    await apiPost('/api/root-folders', {
      name: form.querySelector('[name="name"]').value.trim(),
      kind: form.querySelector('[name="kind"]').value,
      path: form.querySelector('[name="path"]').value.trim(),
      is_default: form.querySelector('[name="is_default"]').checked,
    });
    closeModal();
    invalidateMetaCache(); toast('Carpeta raíz añadida', 'ok');
    await route({ preserveScroll: true });
    return true;
  }
  if (action === 'arr-rf-delete') {
    await apiPost(`/api/root-folders/${element.dataset.id}`, { _delete: true });
    invalidateMetaCache(); toast('Carpeta eliminada', 'ok');
    await route({ preserveScroll: true });
    return true;
  }
  if (action === 'arr-naming-save') {
    const values = {};
    document.querySelectorAll('[data-naming]').forEach((el) => {
      values[el.dataset.naming] = el.type === 'checkbox' ? el.checked : el.value;
    });
    const res = await apiPost('/api/naming', values);
    const ex = res.examples || {};
    const box = $('#namingPreview');
    if (box) {
      box.innerHTML = `
        <div><span class="tiny faint">Película:</span> <code class="tiny mono">${esc(ex.movie || '')}</code></div>
        <div><span class="tiny faint">Serie:</span> <code class="tiny mono">${esc(ex.series || '')}</code></div>
        <div><span class="tiny faint">Anime:</span> <code class="tiny mono">${esc(ex.anime || '')}</code></div>`;
    }
    stateDirtyClear();
    toast('Plantillas de nombrado guardadas', 'ok');
    return true;
  }
  if (action === 'arr-reorg-preview') {
    const preview = await apiPost('/api/naming/reorganize', { execute: false });
    const moves = preview.moves || [];
    const listHtml = moves.length
      ? `<div class="table-wrap" style="max-height:50vh;overflow:auto"><table class="table--stack">
          <thead><tr><th>Título</th><th>Ruta actual</th><th>Nueva ruta</th></tr></thead>
          <tbody>${moves.slice(0, 100).map((m) => `<tr><td>${esc(m.title)}</td><td class="mono tiny">${esc(m.old_path)}</td><td class="mono tiny">${esc(m.new_path)}</td></tr>`).join('')}</tbody>
        </table></div>`
      : empty('Todo está organizado', 'Ningún .strm necesita moverse o renombrarse con las reglas actuales.');
    openModal(
      `Previsualización de Reorganización (${fmtInt(preview.planned || 0)} .strm)`,
      listHtml,
      `<button class="btn btn-ghost" data-action="modal-close">Cerrar</button>
       ${moves.length ? `<button class="btn btn-primary" data-action="arr-reorg-apply">Aplicar cambios</button>` : ''}`,
      { wide: true },
    );
    return true;
  }
  if (action === 'arr-reorg-apply') {
    const res = await apiPost('/api/naming/reorganize', { execute: true });
    closeModal();
    toast(`Reorganizados ${fmtInt(res.moved || 0)} .strm`, 'ok');
    return true;
  }
  if (action === 'arr-tag-add') {
    const name = $('#newTagName')?.value.trim();
    const color = $('#newTagColor')?.value || '#eab64b';
    if (!name) { toast('Escribe un nombre para la etiqueta', 'warn'); return true; }
    await apiPost('/api/tags', { name, color });
    invalidateMetaCache(); toast('Etiqueta creada', 'ok');
    await route({ preserveScroll: true });
    return true;
  }
  if (action === 'arr-tag-delete') {
    await apiPost(`/api/tags/${element.dataset.id}`, { _delete: true });
    invalidateMetaCache(); toast('Etiqueta eliminada', 'ok');
    await route({ preserveScroll: true });
    return true;
  }
  if (action === 'arr-list-new') { openImportListModal(); return true; }
  if (action === 'arr-list-save') {
    const form = $('#arrListForm');
    if (!form) return true;
    await apiPost('/api/import-lists', {
      name: form.querySelector('[name="name"]').value.trim(),
      kind: form.querySelector('[name="kind"]').value,
      source_ref: form.querySelector('[name="source_ref"]').value.trim(),
      media_kind: form.querySelector('[name="media_kind"]').value,
      quality_profile_id: form.querySelector('[name="quality_profile_id"]').value || null,
      root_folder_id: form.querySelector('[name="root_folder_id"]').value || null,
      tag_id: form.querySelector('[name="tag_id"]').value || null,
      monitor_mode: form.querySelector('[name="monitor_mode"]').value || 'all',
    });
    closeModal();
    toast('Import List creada', 'ok');
    await route({ preserveScroll: true });
    return true;
  }
  if (action === 'arr-list-run') {
    element.disabled = true;
    try {
      const res = await apiPost(`/api/import-lists/${element.dataset.id}/run`, {});
      toast(res.status || 'Lista sincronizada', 'ok');
      await route({ preserveScroll: true });
    } finally {
      element.disabled = false;
    }
    return true;
  }
  if (action === 'arr-list-delete') {
    await apiPost(`/api/import-lists/${element.dataset.id}`, { _delete: true });
    toast('Lista eliminada', 'ok');
    await route({ preserveScroll: true });
    return true;
  }
  return false;
}

function stateDirtyClear() {
  const dirty = $('#settingsDirty');
  if (dirty) dirty.classList.add('hidden');
}


export function renderArrSection(sectionKey, data) {
  const full = renderArrSettingsSections(data);
  const wrap = {
    perfiles: ['settings-profiles', 'settings-media'],
    medios: ['settings-media', 'settings-lists'],
    listas: ['settings-lists', '__END__'],
  };
  if (sectionKey === 'perfiles') {
    const start = full.indexOf('<div class="panel section" id="settings-profiles">');
    const end = full.indexOf('<div class="panel section" id="settings-media">');
    return start >= 0 && end > start ? full.slice(start, end) : '';
  }
  if (sectionKey === 'medios') {
    const start = full.indexOf('<div class="panel section" id="settings-media">');
    const end = full.indexOf('<div class="panel section" id="settings-lists">');
    return start >= 0 && end > start ? full.slice(start, end) : '';
  }
  if (sectionKey === 'listas') {
    const start = full.indexOf('<div class="panel section" id="settings-lists">');
    return start >= 0 ? full.slice(start) : '';
  }
  return '';
}
