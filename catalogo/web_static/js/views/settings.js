import { api } from '../api.js';
import { attentionSummary, paintAttention } from '../attention.js';
import { esc, fmtBytes, fmtDate, fmtInt } from '../format.js';
import { state } from '../store.js';
import { $, badge, empty, icon, setHeader, status } from '../ui.js';
import { fetchArrSettingsData, renderArrSection } from './settings_arr.js';

const INTEGRATIONS = [
  { name: 'prowlarr', label: 'Prowlarr', prefix: 'prowlarr' },
  { name: 'jackett', label: 'Jackett', prefix: 'jackett' },
  { name: 'qbittorrent', label: 'qBittorrent', prefix: 'qbittorrent' },
  { name: 'jellyfin', label: 'Jellyfin', prefix: 'jellyfin' },
  { name: 'radarr', label: 'Radarr (solo lectura)', prefix: 'radarr' },
  { name: 'sonarr', label: 'Sonarr (solo lectura)', prefix: 'sonarr' },
  { name: 'trailarr', label: 'Trailarr', prefix: 'trailarr' },
  { name: 'tmdb', label: 'TMDB', prefix: 'tmdb' },
];

function attentionFor(attention, group) {
  return (attention?.items || []).filter((item) => item.group === group && item.severity !== 'info');
}

function sectionDot(attention, group) {
  const items = attentionFor(attention, group);
  if (!items.length) return '';
  const text = items.length === 1 ? 'aviso pendiente' : 'avisos pendientes';
  return `<span class="settings-dot" title="${items.length} ${text}" aria-label="${items.length} ${text}"></span>`;
}

function integrationDot(attention, name) {
  const item = (attention?.items || []).find((entry) => entry.key === `integration:${name}`);
  if (!item) return '';
  return `<span class="settings-dot" title="${esc(item.title)}" aria-label="${esc(item.title)}"></span>`;
}

function sourceBadge(source) {
  if (source === 'db') return badge('web', 'ok');
  if (source === 'env') return badge('entorno', 'muted');
  return badge('por defecto', 'muted');
}

function field(item) {
  const key = esc(item.key);
  const label = `<label for="set-${key}">${esc(item.label)}</label>`;
  const effective = item.effective && item.effective !== item.value
    ? `<span class="tiny faint">Se usa: <span class="mono">${esc(item.effective)}</span></span>`
    : '';
  let input;
  if (item.secret) {
    input = `<input id="set-${key}" class="input" type="password" autocomplete="new-password"
      data-setting="${key}" placeholder="${item.configured ? 'Configurado, dejar vacío para no cambiar' : 'Sin configurar'}">`;
  } else if (item.kind === 'int' || item.kind === 'float') {
    const min = item.minimum !== undefined ? `min="${item.minimum}"` : '';
    const max = item.maximum !== undefined ? `max="${item.maximum}"` : '';
    input = `<input id="set-${key}" class="input" type="number" step="${item.kind === 'float' ? '0.1' : '1'}"
      ${min} ${max} data-setting="${key}" value="${esc(item.value)}">`;
  } else {
    input = `<input id="set-${key}" class="input" type="text"
      data-setting="${key}" value="${esc(item.value)}">`;
  }
  const extra = [];
  if (item.secret) {
    extra.push(item.configured
      ? status(`Configurado ${item.hint || ''}`, 'ok')
      : status('Sin configurar', 'muted'));
    if (item.configured) {
      extra.push(`<button class="btn btn-sm btn-danger" data-action="settings-clear" data-clear-setting="${key}">Borrar</button>`);
    }
  }
  if (item.kind === 'path' || item.kind === 'paths') {
    extra.push(`<button class="btn btn-sm btn-ghost" data-action="settings-path" data-path-setting="${key}" data-effective="${esc(item.effective || '')}">${icon('search', 'sm')} Comprobar</button>`);
  }
  return `<div class="settings-item">
    <div class="settings-label">${label}<span class="tiny faint">${esc(item.help || '')}</span></div>
    <div class="settings-control">
      ${input}
      ${effective}
      <div class="settings-extra">${extra.join('')}${sourceBadge(item.source)}</div>
      <span class="tiny faint path-status" role="status" data-path-for="${key}"></span>
    </div>
  </div>`;
}

function dirField(item, paths) {
  const key = esc(item.key);
  const effective = item.effective && item.effective !== item.value
    ? `<span class="tiny faint">Se usa: <span class="mono">${esc(item.effective)}</span></span>`
    : '';
  return `<div class="dir-item">
    <div class="dir-item-head">
      <label class="dir-label" for="set-${key}">${esc(item.label)}</label>
      ${sourceBadge(item.source)}
    </div>
    <p class="dir-help">${esc(item.help || '')}</p>
    <div class="dir-control">
      <input id="set-${key}" class="input" type="text" data-setting="${key}" value="${esc(item.value)}">
      <button class="btn btn-sm btn-ghost" data-action="settings-path" data-path-setting="${key}" data-effective="${esc(item.effective || '')}">${icon('search', 'sm')} Comprobar</button>
    </div>
    ${effective}
    <div class="dir-status">
      ${diskLine(paths?.[item.key], item.key)}
      <span class="tiny faint path-status" role="status" data-path-for="${key}"></span>
    </div>
  </div>`;
}

function integrationCard(items, meta, paths, attention, allItems = []) {
  const hasWebToken = Boolean(allItems.find((item) => item.key === 'web_token')?.configured);
  const apiKeyHint = hasWebToken
    ? 'tu <strong>Token de acceso web</strong> (sección Seguridad)'
    : '<span class="mono">0123456789abcdef0123456789abcdef</span> (32 caracteres; sin token web activo sirve cualquier clave de 32 caracteres)';
  const bridgeHelp = meta.name === 'trailarr' ? `<div class="tiny faint" style="margin-top:8px">
      Puente *arr hacia carpetas <span class="mono">.strm</span> (para <strong>Trailarr → Settings → Connections</strong>):<br>
      Radarr: <span class="mono">${esc(window.location.origin)}/trailarr/radarr</span><br>
      Sonarr: <span class="mono">${esc(window.location.origin)}/trailarr/sonarr</span><br>
      API Key: ${apiKeyHint}
    </div>` : '';
  return `<div class="panel" id="settings-int-${esc(meta.name)}">
    <div class="panel-head">
      <h2>${integrationDot(attention, meta.name)}${esc(meta.label)}</h2>
      <div class="row">
        <button class="btn btn-sm btn-ghost" data-action="settings-test" data-name="${meta.name}">Probar</button>
      </div>
    </div>
    <div class="panel-body">
      ${items.map(field).join('')}
      ${bridgeHelp}
      <span class="tiny faint" role="status" data-integration-result="${meta.name}"></span>
    </div>
  </div>`;
}

const PARAM_LABELS = {
  limit: 'Límite',
  delay: 'Pausa (s)',
  workers: 'Trabajadores',
  execute: 'Ejecutar de verdad',
  years: 'Años',
  grace: 'Gracia (s)',
};

function paramsEditor(task) {
  const entries = Object.entries(task.params || {});
  if (!entries.length) return '';
  return `<details class="scheduler-params">
    <summary>Parámetros</summary>
    <div class="form-grid">
      ${entries.map(([key, value]) => {
        const input = typeof value === 'boolean'
          ? `<label class="inline check"><input type="checkbox" data-task="${esc(task.name)}" data-param="${esc(key)}" ${value ? 'checked' : ''}> ${esc(PARAM_LABELS[key] || key)}</label>`
          : typeof value === 'number'
            ? `<label class="field"><span>${esc(PARAM_LABELS[key] || key)}</span><input class="input" type="number" data-task="${esc(task.name)}" data-param="${esc(key)}" value="${esc(value)}"></label>`
            : `<label class="field"><span>${esc(PARAM_LABELS[key] || key)}</span><input class="input" type="text" data-task="${esc(task.name)}" data-param="${esc(key)}" value="${esc(value)}"></label>`;
        return input;
      }).join('')}
    </div>
  </details>`;
}

function indexerRow(indexer) {
  return `<div class="kv" data-indexer-row>
    <span>${esc(indexer.name)} <span data-indexer-state>${indexer.enabled === false ? status('Pausado', 'warn') : status('Activo', 'ok')}</span></span>
    <span class="row" style="gap:8px">
      <span class="tiny faint">${fmtInt(indexer.queries)} consultas</span>
      ${indexer.failed ? status(`${fmtInt(indexer.failed)} ${indexer.failed === 1 ? 'fallo' : 'fallos'}`, 'warn') : status('Sin fallos', 'ok')}
      <span class="tiny faint">${fmtInt(indexer.response_ms)} ms</span>
      <button class="btn btn-sm btn-ghost" data-action="monitor-indexer" data-id="${indexer.id}" data-enabled="${indexer.enabled === false ? '0' : '1'}">${indexer.enabled === false ? 'Activar' : 'Pausar'}</button>
    </span>
  </div>`;
}

function schedulerRow(task) {
  const cadence = task.schedule === 'interval'
    ? `<input class="input input-sm" type="number" min="10" step="10" data-task="${esc(task.name)}" data-field="interval" value="${esc(task.interval)}"> s`
    : task.schedule === 'daily'
      ? `<input class="input input-sm" type="time" data-task="${esc(task.name)}" data-field="at" value="${esc(task.at)}">`
      : `<select class="input input-sm" data-task="${esc(task.name)}" data-field="weekday">
          ${['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo']
            .map((day, index) => `<option value="${index}" ${Number(task.weekday) === index ? 'selected' : ''}>${day}</option>`)
            .join('')}
        </select>
        <input class="input input-sm" type="time" data-task="${esc(task.name)}" data-field="at" value="${esc(task.at)}">`;
  const last = task.last;
  const lastState = last
    ? status(last.state === 'ok' ? 'OK' : last.state, last.state === 'ok' ? 'ok' : last.state === 'error' ? 'bad' : 'muted')
    : status('Sin ejecutar', 'muted');
  return `<tr>
    <td data-label="Tarea"><strong>${esc(task.label)}</strong><div class="tiny faint">${esc(task.description)}</div>${paramsEditor(task)}</td>
    <td data-label="Activa"><input type="checkbox" aria-label="Activar ${esc(task.label)}" data-task="${esc(task.name)}" data-field="enabled" ${task.enabled ? 'checked' : ''}></td>
    <td data-label="Cadencia">${cadence}</td>
    <td data-label="Próxima">${task.next_run ? fmtDate(task.next_run) : '—'}</td>
    <td data-label="Última">${lastState}${last?.finished_at ? `<div class="tiny faint">${fmtDate(last.finished_at)}</div>` : ''}</td>
    <td class="actions" data-label="">
      <button class="btn btn-sm btn-ghost" data-action="scheduler-save" data-task="${esc(task.name)}">Guardar</button>
      <button class="btn btn-sm btn-primary" data-action="scheduler-run" data-task="${esc(task.name)}">${icon('play', 'sm')} Ejecutar</button>
    </td>
  </tr>`;
}

const CREATABLE_PATHS = new Set([
  'catalog_library', 'downloads_root', 'seed_root', 'ondemand_save_path',
]);

function diskLine(item, key) {
  if (!item) return '';
  if (item.exists) {
    const state = item.is_dir
      ? (item.writable ? 'Directorio, escribible' : 'Directorio, sin escritura')
      : 'No es un directorio';
    return `<span class="tiny ${item.writable ? 'ok-text' : 'warn-text'}">${esc(state)}, ${fmtBytes(item.free)} libres</span>`;
  }
  const parent = item.checked_parent || '—';
  if (!item.writable) {
    return `<span class="tiny warn-text">No existe y la carpeta padre ${esc(parent)} no permite escribir.</span>`;
  }
  if (CREATABLE_PATHS.has(key)) {
    return `<span class="tiny warn-text">No existe; se creará dentro de ${esc(parent)}, ${fmtBytes(item.free || 0)} libres.</span>`;
  }
  return `<span class="tiny warn-text">No existe. La carpeta padre ${esc(parent)} existe (${fmtBytes(item.free || 0)} libres), pero esta ruta no. Créala o corrige la ruta.</span>`;
}

export async function renderSettings({ signal, query } = {}) {
  const [data, schedule, attention, seed, searchFallback, language, system, indexers, setupData, arrData] = await Promise.all([
    api('/api/settings', { signal }),
    api('/api/scheduler', { signal }),
    api('/api/attention', { signal }).catch(() => null),
    api('/api/seed', { signal }).catch(() => null),
    api('/api/search-fallback', { signal }).catch(() => null),
    api('/api/language', { signal }).catch(() => null),
    api('/api/system', { signal }).catch(() => null),
    api('/api/indexers', { signal }).catch(() => ({ items: [] })),
    api('/api/setup', { signal }).catch(() => null),
    fetchArrSettingsData(signal),
  ]);
  state.attention = attention;
  paintAttention(attention);
  const languagePreference = language?.preference || 'any';
  setHeader('Configuración');
  const items = data.settings || [];
  const integrationKeys = new Set(INTEGRATIONS.flatMap((meta) =>
    items.filter((item) => item.key.startsWith(`${meta.prefix}_`)).map((item) => item.key)));
  const otherIntegrations = items.filter((item) => item.section === 'integraciones' && !integrationKeys.has(item.key));
  const dirs = items.filter((item) => item.section === 'directorios');
  const dirKeys = (...keys) => dirs.filter((item) => keys.includes(item.key));
  const setupSteps = setupData?.steps || {};
  const setup = [
    {
      key: 'qbittorrent',
      label: 'Conectar qBittorrent',
      help: 'Descarga, siembra y sirve los torrents.',
      done: Boolean(setupSteps.qbittorrent?.done),
      target: 'settings-integrations',
    },
    {
      key: 'sources',
      label: 'Configurar Prowlarr o Jackett',
      help: 'Basta con uno de los dos para descubrir fuentes.',
      done: Boolean(setupSteps.sources?.done),
      target: 'settings-integrations',
    },
    {
      key: 'directories',
      label: 'Revisar los directorios',
      help: 'Accesos .strm, descargas y biblioteca existente.',
      done: Boolean(setupSteps.directories?.done),
      target: 'settings-directories',
    },
    {
      key: 'jellyfin',
      label: 'Conectar Jellyfin',
      help: 'Opcional: es donde se ven los títulos.',
      done: Boolean(setupSteps.jellyfin?.done),
      target: 'settings-integrations',
    },
  ];
  const setupDone = setup.filter((step) => step.done).length;
  const playback = items.filter((item) => item.section === 'reproduccion');
  const security = items.filter((item) => item.section === 'seguridad');
  const seedDefaults = seed?.defaults || { min_hours: 72, ratio: 1, force: true, allow_delete: false };
  const policyByTracker = new Map(
    (seed?.policies || []).map((item) => [item.tracker, item]),
  );
  const systemTrackers = new Map(
    (system?.trackers || []).map((item) => [item.tracker, item]),
  );
  const seedTrackers = [
    ...new Set([
      ...(seed?.known || []).map((item) => item.tracker),
      ...(seed?.policies || []).map((item) => item.tracker),
      ...systemTrackers.keys(),
    ]),
  ].filter(Boolean).sort((a, b) => a.localeCompare(b, 'es'));
  const trackerRows = seedTrackers.map((tracker) => {
    const current = policyByTracker.get(tracker) || {};
    const known = (seed?.known || []).find((item) => item.tracker === tracker) || {};
    const stats = systemTrackers.get(tracker) || {};
    const hours = current.min_seed_hours ?? seedDefaults.min_hours;
    const days = Math.round((Number(hours) / 24) * 100) / 100;
    const ratio = current.target_ratio ?? seedDefaults.ratio;
    const privacy = stats.privacy || known.privacy || current.privacy || 'private';
    const isBlocked = stats.enabled !== undefined
      ? Number(stats.enabled) === 0
      : current.enabled !== undefined && Number(current.enabled) === 0;
    const enabled = !isBlocked;
    const hardBlocked = isBlocked && Number(stats.blocked || 0) > 0;
    const versiones = Number(stats.releases ?? known.releases ?? 0);
    // Los trackers eliminados quedan bloqueados en la BD para que los syncs
    // no los reincorporen, pero no se muestran: no tienen versiones.
    if (isBlocked && versiones === 0) return '';
    const detail = [
      stats.privacy || known.privacy || current.privacy || '',
      stats.releases !== undefined
        ? `${fmtInt(stats.releases)} versiones`
        : known.releases ? `${fmtInt(known.releases)} versiones` : '',
      stats.alive !== undefined ? `${fmtInt(stats.alive)} vivos` : '',
      stats.blocked ? `${fmtInt(stats.blocked)} bloqueados` : '',
    ].filter(Boolean).join(', ');
    return `<tr>
      <td data-label="Tracker"><strong>${esc(tracker)}</strong>
        <div class="tiny faint">${esc(detail)}</div>
      </td>
      <td data-label="Privacidad"><select class="input input-sm"
        data-seed-tracker="${esc(tracker)}" data-seed-field="privacy"
        aria-label="Privacidad de ${esc(tracker)}">
        <option value="private" ${privacy === 'public' ? '' : 'selected'}>Privado</option>
        <option value="public" ${privacy === 'public' ? 'selected' : ''}>Público</option>
      </select></td>
      <td data-label="Días"><input class="input input-sm" type="number" min="0" max="365" step="0.5"
        data-seed-tracker="${esc(tracker)}" data-seed-field="min_days" value="${esc(days)}"></td>
      <td data-label="Ratio"><input class="input input-sm" type="number" min="0" max="100" step="0.1"
        data-seed-tracker="${esc(tracker)}" data-seed-field="target_ratio" value="${esc(ratio)}"></td>
      <td data-label="Descargar"><input type="checkbox" aria-label="Permitir descargas de ${esc(tracker)}"
        data-seed-tracker="${esc(tracker)}" data-seed-field="enabled" ${enabled ? 'checked' : ''} ${hardBlocked ? 'disabled title="Bloqueado: usa Desbloquear"' : ''}></td>
      <td data-label="Última" class="tiny faint nowrap">${stats.last_checked ? fmtDate(stats.last_checked) : '—'}</td>
      <td class="actions" data-label="">
        <button class="btn btn-sm btn-ghost" data-action="seed-policy-save" data-tracker="${esc(tracker)}">Guardar</button>
        ${isBlocked ? `<button class="btn btn-sm" data-action="tracker-block" data-tracker="${esc(tracker)}">Desbloquear</button>` : ''}
        <button class="btn btn-sm btn-ghost" data-action="tracker-strm" data-tracker="${esc(tracker)}">Borrar .strm</button>
        <button class="btn btn-sm btn-danger" data-action="tracker-delete" data-tracker="${esc(tracker)}">Eliminar</button>
      </td>
    </tr>`;
  }).join('');

  const rawSection = query?.get('section') || 'general';
  const sectionMap = {
    general: 'general',
    directorios: 'general',
    reproduccion: 'general',
    idioma: 'general',
    conexiones: 'conexiones',
    integraciones: 'conexiones',
    indexers: 'conexiones',
    perfiles: 'perfiles',
    formatos: 'perfiles',
    avanzado: 'avanzado',
    seguridad: 'avanzado',
    medios: 'avanzado',
    listas: 'avanzado',
    siembra: 'avanzado',
    tareas: 'avanzado',
  };
  const activeTab = sectionMap[rawSection] || 'general';
  const tabs = [
    ['general', 'General'],
    ['conexiones', 'Conexiones'],
    ['perfiles', 'Perfiles'],
    ['avanzado', 'Avanzado y Sistema'],
  ];
  const showGlobalSave = activeTab === 'general' || activeTab === 'conexiones' || activeTab === 'avanzado';

  const setupHtml = setupDone < setup.length ? `<div class="panel section" id="settings-setup">
      <div class="panel-head">
        <h2>Primeros pasos</h2>
        <span class="right tiny faint">${setupDone} de ${setup.length} listos</span>
        <a class="btn btn-sm btn-primary" href="#/setup">Abrir asistente</a>
      </div>
      <div class="panel-body">
        <div class="setup-list">
          ${setup.map((step) => `<div class="setup-step ${step.done ? 'done' : ''}">
            <span class="setup-mark">${step.done ? icon('check', 'sm') : ''}</span>
            <div class="setup-copy">
              <strong>${esc(step.label)}</strong>
              <span class="tiny faint">${esc(step.help)}</span>
            </div>
            ${step.done
              ? status('Hecho', 'ok')
              : `<button class="btn btn-sm" data-action="settings-jump" data-target="${step.target}">Configurar</button>`}
          </div>`).join('')}
        </div>
      </div>
    </div>` : '';

  $('#view').innerHTML = `
    <div class="panel pad section">
      <div class="row between" style="align-items:flex-start">
        <div style="flex:1;min-width:280px">
          <strong>Configuración de TOD</strong>
          <p class="muted tiny" style="margin:4px 0 0">Navega por categorías sin listas largas. Los cambios se aplican al guardar.</p>
        </div>
        <div class="row">
          <a href="#/system" title="Ver avisos en Estado" style="text-decoration:none">${attentionSummary(attention)}</a>
          <span id="settingsDirty" class="status warn ${state.settingsDirty ? '' : 'hidden'}">Cambios sin guardar</span>
          ${showGlobalSave ? `<button class="btn btn-primary" data-action="settings-save">${icon('check', 'sm')} Guardar configuración</button>` : ''}
        </div>
      </div>
      ${data.restart_required?.length ? `<div class="notice">Requieren reinicio: ${data.restart_required.map((key) => esc(items.find((item) => item.key === key)?.label || key)).join(', ')}</div>` : ''}
      <nav class="settings-nav" role="tablist" aria-label="Secciones de configuración">
        ${tabs.map(([id, label]) => `<button class="settings-tab ${activeTab === id ? 'active' : ''}" data-action="settings-tab" data-section="${id}" role="tab" aria-selected="${activeTab === id}">${esc(label)}</button>`).join('')}
      </nav>
    </div>
    ${activeTab === 'general' ? setupHtml : ''}
    ${activeTab === 'conexiones' ? `



    <div class="section" id="settings-integrations">
      <div class="panel-head"><h2>${sectionDot(attention, 'integraciones')}Conexiones</h2><span class="right tiny faint settings-hint">Prowlarr, Jackett, qBittorrent, Jellyfin, Radarr/Sonarr, Trailarr</span></div>
      <div class="settings-grid">
        ${INTEGRATIONS.map((meta) => integrationCard(
          items.filter((item) => item.key.startsWith(`${meta.prefix}_`)), meta, data.paths, attention, items,
        )).join('')}
        ${otherIntegrations.length ? integrationCard(otherIntegrations, { name: 'otros', label: 'Otros servicios' }, data.paths, attention) : ''}
      </div>
    </div>

    <div class="panel section" id="settings-indexers">
      <div class="panel-head">
        <h2>Indexers</h2>
        <span class="right tiny faint">${indexers.enabled || 0} de ${indexers.total || 0} activos</span>
      </div>
      <div class="panel-body" style="padding-top:4px;padding-bottom:8px">
        <p class="muted tiny" style="margin-top:0">Pausar un indexer lo desactiva en Prowlarr y afecta también a Radarr y Sonarr. Las estadísticas se leen de Prowlarr.</p>
        ${indexers.error ? `<div class="notice">${esc(indexers.error)}</div>` : ''}
        ${(indexers.items || []).length
          ? indexers.items.map(indexerRow).join('')
          : empty('Sin indexers', 'Configura Prowlarr o Jackett para descubrir fuentes.')}
      </div>
    </div>

` : ''}
    ${activeTab === 'general' ? `
    <div class="panel section" id="settings-directories">
      <div class="panel-head"><h2>${sectionDot(attention, 'directorios')}Directorios</h2>
        <button class="btn btn-sm btn-ghost" data-action="settings-test" data-name="directories">Comprobar todo</button>
      </div>
      <div class="panel-body">
        <p class="muted tiny" style="margin-top:0">TOD solo lee la biblioteca de Radarr/Sonarr: no mueve esos ficheros. Lo único que mueve son los torrents on-demand terminados, al Archivo de torrents.</p>
        <h3 class="dir-group">Accesos para Jellyfin</h3>
        ${dirKeys('catalog_library').map((item) => dirField(item, data.paths)).join('')}
        <h3 class="dir-group">Biblioteca existente</h3>
        ${dirKeys('library_root_movies', 'library_root_series').map((item) => dirField(item, data.paths)).join('')}
        <p class="tiny faint dir-note">TOD calcula solo la carpeta común a Películas y Series para saber qué está en la nube.</p>
        <h3 class="dir-group">Descargas y archivo</h3>
        ${dirKeys('downloads_root', 'ondemand_save_path', 'move_target').map((item) => dirField(item, data.paths)).join('')}
        <span class="tiny faint" data-integration-result="directories"></span>
      </div>
    </div>

    <div class="panel section" id="settings-playback">
      <div class="panel-head"><h2>Reproducción</h2></div>
      <div class="panel-body">${playback.map(field).join('')}</div>
    </div>

    <div class="panel section" id="settings-language">
      <div class="panel-head">
        <h2>Idioma de las fuentes</h2>
        <span class="right tiny faint">no cambia el catálogo</span>
      </div>
      <div class="panel-body">
        <div class="form-grid">
          <label class="field"><span>Preferencia</span>
            <select class="input" data-language>
              <option value="any" ${languagePreference === 'any' ? 'selected' : ''}>Cualquier idioma</option>
              <option value="castellano_first" ${languagePreference === 'castellano_first' ? 'selected' : ''}>Castellano primero; si no, V.O. (sin doblaje latino)</option>
              <option value="only_castellano" ${languagePreference === 'only_castellano' ? 'selected' : ''}>Solo castellano (España)</option>
            </select>
          </label>
          <label class="field"><span>&nbsp;</span>
            <button class="btn btn-primary" data-action="language-save">${icon('check', 'sm')} Guardar idioma</button>
          </label>
        </div>
        <p class="muted tiny" style="margin:10px 0 0">
          «Castellano primero» usa el país de origen cuando el título es
          originalmente español: castellano si es de España y latino si es de
          Latinoamérica. En el resto de títulos prefiere castellano de España,
          luego V.O./VOSE, y nunca un doblaje latino. «Solo castellano» solo
          acepta castellano de España. Esto no pausa indexers: para eso usa
          Configuración → Conexiones.
        </p>
      </div>
    </div>

` : ''}
    ${activeTab === 'perfiles' ? renderArrSection('perfiles', arrData) : ''}
    ${activeTab === 'avanzado' ? `
    <div class="panel section" id="settings-security">
      <div class="panel-head"><h2>${sectionDot(attention, 'seguridad')}Seguridad y acceso</h2></div>
      <div class="panel-body">${security.map(field).join('')}</div>
    </div>

    <div class="panel section" id="settings-search-fallback">
      <div class="panel-head"><h2>Búsqueda de respaldo en Radarr/Sonarr</h2></div>
      <div class="panel-body">
        <label class="inline check">
          <input type="checkbox" data-search-field="arr_fallback" ${searchFallback?.arr_fallback ? 'checked' : ''}>
          Buscar los títulos monitorizados sin fichero ni en cola
        </label>
        <p class="muted tiny" style="margin:8px 0">
          Si Radarr o Sonarr pierde un fichero, TOD busca fuentes para ese
          título y publica sus accesos. No mueve ni borra nada en el *arr.
          Se ejecuta en cada espejo y en la pasada de respaldo.
        </p>
        <button class="btn btn-sm btn-primary" data-action="search-save">Guardar búsqueda</button>
      </div>
    </div>

    ${renderArrSection('medios', arrData)}
    ${renderArrSection('listas', arrData)}

    <div class="panel section" id="settings-seed">
      <div class="panel-head">
        <h2>Trackers y siembra</h2>
      </div>
      <div class="panel-body">
        <p class="muted tiny" style="margin-top:0">
          TOD siembra el mínimo y luego suelta el torrent. El borrado lo decide
          qBittorrent salvo que actives «Permitir borrar desde TOD».
        </p>
        <div class="form-grid">
          <label class="field"><span>Días mínimos por defecto</span>
            <input class="input" type="number" min="0" max="365" step="0.5" data-seed-field="min_days" value="${esc(Math.round((Number(seedDefaults.min_hours) / 24) * 100) / 100)}">
          </label>
          <label class="field"><span>Ratio mínimo por defecto</span>
            <input class="input" type="number" min="0" max="100" step="0.1" data-seed-field="ratio" value="${esc(seedDefaults.ratio)}">
          </label>
          <label class="inline check" style="align-self:end;padding-bottom:4px">
            <input type="checkbox" data-seed-field="force" ${seedDefaults.force ? 'checked' : ''}>
            Forzar siembra hasta el mínimo
          </label>
          <label class="inline check" style="align-self:end;padding-bottom:4px">
            <input type="checkbox" data-seed-field="allow_delete" ${seedDefaults.allow_delete ? 'checked' : ''}>
            Permitir borrar desde TOD
          </label>
          <label class="inline check" style="align-self:end;padding-bottom:4px">
            <input type="checkbox" data-seed-field="keep_until_library" ${seedDefaults.keep_until_library !== false ? 'checked' : ''}>
            Mantener hasta que esté en Radarr/Sonarr
          </label>
          <label class="field"><span>Máximo de espera (horas)</span>
            <input class="input" type="number" min="0" max="8760" step="1" data-seed-field="keep_max_hours" value="${esc(seedDefaults.keep_max_hours ?? 72)}">
          </label>
          <label class="field"><span>&nbsp;</span>
            <button class="btn btn-primary" data-action="seed-save">${icon('check', 'sm')} Guardar siembra</button>
          </label>
        </div>
        <p class="muted tiny" style="margin:0 0 8px">
          «Mantener hasta que esté en Radarr/Sonarr» es el seguro para no borrar la única copia mientras el arr no tenga el fichero.
        </p>
        <div class="table-wrap">
          <table class="table--stack">
            <thead><tr><th>Tracker</th><th>Privacidad</th><th>Días</th><th>Ratio</th><th>Descargar</th><th title="Última comprobación de salud">Última</th><th></th></tr></thead>
            <tbody>${trackerRows || '<tr><td colspan="7" class="tiny faint">Aún no hay trackers indexados.</td></tr>'}</tbody>
          </table>
        </div>
        <p class="muted tiny" style="margin:8px 0 0">
          «Descargar» solo permite o bloquea descargas nuevas de TOD en ese
          tracker; no pausa el indexer (para eso, usa Configuración → Conexiones).
          «Eliminar» borra sus versiones y, si lo eliges, sus
          <span class="mono">.strm</span>.
        </p>
      </div>
    </div>

    <div class="panel flush section" id="settings-scheduler">
      <div class="panel-head"><h2>${sectionDot(attention, 'tareas')}Tareas programadas</h2><span class="right tiny faint">${schedule.items?.length || 0} tareas</span></div>
      <div class="table-wrap">
        <table class="table--stack">
          <thead><tr><th>Tarea</th><th>Activa</th><th>Cadencia</th><th>Próxima</th><th>Última</th><th></th></tr></thead>
          <tbody>${(schedule.items || []).map(schedulerRow).join('')}</tbody>
        </table>
      </div>
    </div>

` : ''}
    <div class="row between section settings-footer">
      <span class="tiny faint">Los cambios se guardan en la configuración local de TOD.</span>
    </div>
  `;

  if (!state.settingsDirtyHandler) {
    state.settingsDirtyHandler = (event) => {
      if (state.view !== 'settings') return;
      if (!event.target.closest('[data-setting], [data-task], [data-seed-field], [data-seed-tracker], [data-search-field], [data-language]')) return;
      state.settingsDirty = true;
      $('#settingsDirty')?.classList.remove('hidden');
    };
    document.addEventListener('input', state.settingsDirtyHandler);
    document.addEventListener('change', state.settingsDirtyHandler);
  }

  const focus = query?.get('focus');
  if (focus) {
    requestAnimationFrame(() => {
      const input = document.querySelector(`input[data-setting="${CSS.escape(focus)}"], select[data-setting="${CSS.escape(focus)}"]`);
      if (!input) return;
      input.closest('.settings-item')?.classList.add('attention-focus');
      input.focus({ preventScroll: true });
    });
  }
}
