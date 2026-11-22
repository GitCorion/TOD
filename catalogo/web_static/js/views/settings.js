import { api } from '../api.js';
import { attentionSummary } from '../attention.js';
import { esc, fmtBytes, fmtDate, fmtInt } from '../format.js';
import { state } from '../store.js';
import { $, badge, icon, setHeader, status } from '../ui.js';

const INTEGRATIONS = [
  { name: 'prowlarr', label: 'Prowlarr', prefix: 'prowlarr' },
  { name: 'jackett', label: 'Jackett', prefix: 'jackett' },
  { name: 'qbittorrent', label: 'qBittorrent', prefix: 'qbittorrent' },
  { name: 'jellyfin', label: 'Jellyfin', prefix: 'jellyfin' },
  { name: 'radarr', label: 'Radarr (solo lectura)', prefix: 'radarr' },
  { name: 'sonarr', label: 'Sonarr (solo lectura)', prefix: 'sonarr' },
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
  return badge('defecto', 'muted');
}

function field(item) {
  const key = esc(item.key);
  const label = `<label for="set-${key}">${esc(item.label)}</label>`;
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
      extra.push(`<button class="btn btn-sm btn-danger" data-action="settings-clear" data-setting="${key}">Borrar</button>`);
    }
  }
  if (item.kind === 'path' || item.kind === 'paths') {
    extra.push(`<button class="btn btn-sm btn-ghost" data-action="settings-path" data-setting="${key}">${icon('search', 'sm')} Comprobar</button>`);
  }
  return `<div class="settings-item">
    <div class="settings-label">${label}<span class="tiny faint">${esc(item.help || '')}</span></div>
    <div class="settings-control">
      ${input}
      <div class="settings-extra">${extra.join('')}${sourceBadge(item.source)}</div>
      <span class="tiny faint path-status" role="status" data-path-for="${key}"></span>
    </div>
  </div>`;
}

function integrationCard(items, meta, paths, attention) {
  return `<div class="panel" id="settings-int-${esc(meta.name)}">
    <div class="panel-head">
      <h2>${integrationDot(attention, meta.name)}${esc(meta.label)}</h2>
      <div class="row">
        <button class="btn btn-sm btn-ghost" data-action="settings-test" data-name="${meta.name}">Probar</button>
      </div>
    </div>
    <div class="panel-body">
      ${items.map(field).join('')}
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
    return `<span class="tiny ${item.writable ? 'ok-text' : 'warn-text'}">${esc(state)} · ${fmtBytes(item.free)} libres</span>`;
  }
  const parent = item.checked_parent || '—';
  if (!item.writable) {
    return `<span class="tiny warn-text">No existe y la carpeta padre ${esc(parent)} no permite escribir.</span>`;
  }
  if (CREATABLE_PATHS.has(key)) {
    return `<span class="tiny warn-text">No existe; se creará dentro de ${esc(parent)} · ${fmtBytes(item.free || 0)} libres.</span>`;
  }
  return `<span class="tiny warn-text">No existe. La carpeta padre ${esc(parent)} existe (${fmtBytes(item.free || 0)} libres), pero esta ruta no. Créala o corrige la ruta.</span>`;
}

export async function renderSettings({ signal, query } = {}) {
  const [data, schedule, attention, seed] = await Promise.all([
    api('/api/settings', { signal }),
    api('/api/scheduler', { signal }),
    api('/api/attention', { signal }).catch(() => null),
    api('/api/seed', { signal }).catch(() => null),
  ]);
  state.attention = attention;
  setHeader('Configuración', 'Sistema');
  const items = data.settings || [];
  const integrationKeys = new Set(INTEGRATIONS.flatMap((meta) =>
    items.filter((item) => item.key.startsWith(`${meta.prefix}_`)).map((item) => item.key)));
  const otherIntegrations = items.filter((item) => item.section === 'integraciones' && !integrationKeys.has(item.key));
  const dirs = items.filter((item) => item.section === 'directorios');
  const playback = items.filter((item) => item.section === 'reproduccion');
  const security = items.filter((item) => item.section === 'seguridad');
  const seedDefaults = seed?.defaults || { min_hours: 50 * 24, ratio: 1, force: true, allow_delete: false };
  const policyByTracker = new Map(
    (seed?.policies || []).map((item) => [item.tracker, item]),
  );
  const seedTrackers = [
    ...new Set([
      ...(seed?.known || []).map((item) => item.tracker),
      ...(seed?.policies || []).map((item) => item.tracker),
    ]),
  ].filter(Boolean).sort((a, b) => a.localeCompare(b, 'es'));
  const trackerRows = seedTrackers.map((tracker) => {
    const current = policyByTracker.get(tracker) || {};
    const known = (seed?.known || []).find((item) => item.tracker === tracker) || {};
    const hours = current.min_seed_hours ?? seedDefaults.min_hours;
    const days = Math.round((Number(hours) / 24) * 100) / 100;
    const ratio = current.target_ratio ?? seedDefaults.ratio;
    const enabled = current.enabled !== false;
    return `<tr>
      <td data-label="Tracker"><strong>${esc(tracker)}</strong>
        <div class="tiny faint">${esc(known.privacy || current.privacy || '')}${known.releases ? ` · ${fmtInt(known.releases)} releases` : ''}</div>
      </td>
      <td data-label="Días"><input class="input input-sm" type="number" min="0" max="365" step="0.5"
        data-seed-tracker="${esc(tracker)}" data-seed-field="min_days" value="${esc(days)}"></td>
      <td data-label="Ratio"><input class="input input-sm" type="number" min="0" max="100" step="0.1"
        data-seed-tracker="${esc(tracker)}" data-seed-field="target_ratio" value="${esc(ratio)}"></td>
      <td data-label="Activa"><input type="checkbox" aria-label="Activar política de ${esc(tracker)}"
        data-seed-tracker="${esc(tracker)}" data-seed-field="enabled" ${enabled ? 'checked' : ''}></td>
      <td class="actions" data-label="">
        <button class="btn btn-sm btn-ghost" data-action="seed-policy-save" data-tracker="${esc(tracker)}">Guardar</button>
      </td>
    </tr>`;
  }).join('');

  $('#view').innerHTML = `
    <div class="panel pad section">
      <div class="row between" style="align-items:flex-start">
        <div style="flex:1;min-width:280px">
          <strong>Integraciones y biblioteca</strong>
          <p class="muted tiny" style="margin:4px 0 0">Conecta qBittorrent, Prowlarr/Jackett y Jellyfin. Radarr/Sonarr se espejan automáticamente. Los cambios se aplican al guardar; la URL de reproducción requiere reinicio.</p>
        </div>
        <div class="row">
          <a href="#/system" title="Ver avisos en Estado" style="text-decoration:none">${attentionSummary(attention)}</a>
          <span id="settingsDirty" class="status warn ${state.settingsDirty ? '' : 'hidden'}">Cambios sin guardar</span>
          <button class="btn btn-primary" data-action="settings-save">${icon('check', 'sm')} Guardar configuración</button>
        </div>
      </div>
      ${data.restart_required?.length ? `<div class="notice">Requieren reinicio: ${data.restart_required.map((key) => esc(items.find((item) => item.key === key)?.label || key)).join(', ')}</div>` : ''}
      <nav class="chips settings-nav" aria-label="Secciones de configuración">
        <button class="chip" data-action="settings-jump" data-target="settings-integrations">Integraciones</button>
        <button class="chip" data-action="settings-jump" data-target="settings-directories">Directorios</button>
        <button class="chip" data-action="settings-jump" data-target="settings-playback">Reproducción</button>
        <button class="chip" data-action="settings-jump" data-target="settings-security">Seguridad</button>
        <button class="chip" data-action="settings-jump" data-target="settings-seed">Siembra</button>
        <button class="chip" data-action="settings-jump" data-target="settings-scheduler">Tareas</button>
      </nav>
    </div>

    <div class="section" id="settings-integrations">
      <div class="panel-head"><h2>${sectionDot(attention, 'integraciones')}Integraciones</h2><span class="right tiny faint settings-hint">Prowlarr, Jackett, qBittorrent, Jellyfin, Radarr/Sonarr</span></div>
      <div class="settings-grid">
        ${INTEGRATIONS.map((meta) => integrationCard(
          items.filter((item) => item.key.startsWith(`${meta.prefix}_`)), meta, data.paths, attention,
        )).join('')}
        ${otherIntegrations.length ? integrationCard(otherIntegrations, { name: 'otros', label: 'Otros servicios' }, data.paths, attention) : ''}
      </div>
    </div>

    <div class="grid grid-2 section" id="settings-directories">
      <div class="panel">
        <div class="panel-head"><h2>${sectionDot(attention, 'directorios')}Directorios</h2>
          <button class="btn btn-sm btn-ghost" data-action="settings-test" data-name="directories">Comprobar todo</button>
        </div>
        <div class="panel-body">
          ${dirs.map((item) => `${field(item)}${diskLine(data.paths?.[item.key], item.key)}`).join('')}
          <span class="tiny faint" data-integration-result="directories"></span>
        </div>
      </div>
      <div class="panel" id="settings-playback">
        <div class="panel-head"><h2>Reproducción</h2></div>
        <div class="panel-body">${playback.map(field).join('')}</div>
      </div>
    </div>

    <div class="panel section" id="settings-security">
      <div class="panel-head"><h2>${sectionDot(attention, 'seguridad')}Seguridad</h2></div>
      <div class="panel-body">${security.map(field).join('')}</div>
    </div>

    <div class="panel section" id="settings-seed">
      <div class="panel-head">
        <h2>Siembra</h2>
        <span class="right tiny faint">TOD no borra torrents ni ficheros</span>
      </div>
      <div class="panel-body">
        <p class="muted tiny" style="margin-top:0">
          TOD mantiene el torrent en force-start hasta cumplir el mínimo. Después
          lo suelta y qBittorrent aplica sus propias reglas de ratio y tiempo.
          Por defecto qBittorrent decide el borrado; puedes permitir que TOD
          también borre desde Descargas.
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
          <label class="field"><span>&nbsp;</span>
            <button class="btn btn-primary" data-action="seed-save">${icon('check', 'sm')} Guardar siembra</button>
          </label>
        </div>
        <div class="table-wrap" style="margin-top:14px">
          <table class="table--stack">
            <thead><tr><th>Tracker</th><th>Días</th><th>Ratio</th><th>Activa</th><th></th></tr></thead>
            <tbody>${trackerRows || '<tr><td colspan="5" class="tiny faint">Aún no hay trackers indexados.</td></tr>'}</tbody>
          </table>
        </div>
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

    <div class="row between section settings-footer">
      <span class="tiny faint">Los cambios se guardan en la configuración local de TOD.</span>
      <button class="btn btn-primary" data-action="settings-save">${icon('check', 'sm')} Guardar configuración</button>
    </div>
  `;

  if (!state.settingsDirtyHandler) {
    state.settingsDirtyHandler = (event) => {
      if (state.view !== 'settings') return;
      if (!event.target.closest('[data-setting], [data-task], [data-seed-field], [data-seed-tracker]')) return;
      state.settingsDirty = true;
      $('#settingsDirty')?.classList.remove('hidden');
    };
    document.addEventListener('input', state.settingsDirtyHandler);
    document.addEventListener('change', state.settingsDirtyHandler);
  }

  const sections = {
    integraciones: 'settings-integrations',
    directorios: 'settings-directories',
    reproduccion: 'settings-playback',
    seguridad: 'settings-security',
    siembra: 'settings-seed',
    tareas: 'settings-scheduler',
  };
  const target = sections[query?.get('section')];
  if (target) {
    requestAnimationFrame(() => {
      document.getElementById(target)?.scrollIntoView({
        behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',
        block: 'start',
      });
      const focus = query.get('focus');
      if (!focus) return;
      const input = document.querySelector(`[data-setting="${CSS.escape(focus)}"]`);
      if (!input) return;
      input.closest('.settings-item')?.classList.add('attention-focus');
      input.focus({ preventScroll: true });
    });
  }
}
