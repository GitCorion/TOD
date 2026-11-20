import { api } from '../api.js';
import { esc, fmtBytes, fmtDate } from '../format.js';
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

function integrationCard(items, meta, paths) {
  return `<div class="panel">
    <div class="panel-head">
      <h2>${esc(meta.label)}</h2>
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
    <td data-label="Tarea"><strong>${esc(task.label)}</strong><div class="tiny faint">${esc(task.description)}</div></td>
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

function diskLine(item) {
  if (!item) return '';
  const label = item.exists
    ? `${item.exists ? 'existe' : 'no existe'}${item.is_dir ? ', directorio' : ''}${item.writable ? ', escribible' : ', sin escritura'} · ${fmtBytes(item.free)} libres`
    : `no existe; se creará dentro de ${esc(item.checked_parent || '—')} · ${fmtBytes(item.free || 0)} libres`;
  return `<span class="tiny ${item.exists && item.writable ? 'ok-text' : 'warn-text'}">${esc(label)}</span>`;
}

export async function renderSettings({ signal } = {}) {
  const [data, schedule] = await Promise.all([
    api('/api/settings', { signal }),
    api('/api/scheduler', { signal }),
  ]);
  setHeader('Configuración', 'TOD');
  const items = data.settings || [];
  const integrationKeys = new Set(INTEGRATIONS.flatMap((meta) =>
    items.filter((item) => item.key.startsWith(`${meta.prefix}_`)).map((item) => item.key)));
  const otherIntegrations = items.filter((item) => item.section === 'integraciones' && !integrationKeys.has(item.key));
  const dirs = items.filter((item) => item.section === 'directorios');
  const playback = items.filter((item) => item.section === 'reproduccion');
  const security = items.filter((item) => item.section === 'seguridad');

  $('#view').innerHTML = `
    <div class="panel pad section">
      <div class="row row-wrap">
        <div>
          <strong>Servicio único</strong>
          <p class="muted tiny" style="margin:4px 0 0">Web, API, reproducción y scheduler comparten proceso, base de datos y configuración. Los cambios se aplican sin reiniciar salvo los marcados como reinicio.</p>
        </div>
        <button class="btn btn-primary" data-action="settings-save">${icon('check', 'sm')} Guardar configuración</button>
      </div>
      ${data.restart_required?.length ? `<div class="notice">Requieren reinicio: ${data.restart_required.map((key) => esc(items.find((item) => item.key === key)?.label || key)).join(', ')}</div>` : ''}
    </div>

    <div class="section">
      <div class="panel-head"><h2>Integraciones</h2><span class="right tiny faint settings-hint">Prowlarr, Jackett, qBittorrent, Jellyfin, Radarr/Sonarr</span></div>
      <div class="settings-grid">
        ${INTEGRATIONS.map((meta) => integrationCard(
          items.filter((item) => item.key.startsWith(`${meta.prefix}_`)), meta, data.paths,
        )).join('')}
        ${otherIntegrations.length ? integrationCard(otherIntegrations, { name: 'otros', label: 'Otros servicios' }, data.paths) : ''}
      </div>
    </div>

    <div class="grid grid-2 section">
      <div class="panel">
        <div class="panel-head"><h2>Directorios</h2>
          <button class="btn btn-sm btn-ghost" data-action="settings-test" data-name="directories">Comprobar todo</button>
        </div>
        <div class="panel-body">
          ${dirs.map((item) => `${field(item)}${diskLine(data.paths?.[item.key])}`).join('')}
          <span class="tiny faint" data-integration-result="directories"></span>
        </div>
      </div>
      <div class="panel">
        <div class="panel-head"><h2>Reproducción</h2></div>
        <div class="panel-body">${playback.map(field).join('')}</div>
      </div>
    </div>

    <div class="panel section">
      <div class="panel-head"><h2>Seguridad</h2></div>
      <div class="panel-body">${security.map(field).join('')}</div>
    </div>

    <div class="panel flush section">
      <div class="panel-head"><h2>Tareas programadas</h2><span class="right tiny faint">${schedule.items?.length || 0} tareas</span></div>
      <div class="table-wrap">
        <table class="table--stack">
          <thead><tr><th>Tarea</th><th>Activa</th><th>Cadencia</th><th>Próxima</th><th>Última</th><th></th></tr></thead>
          <tbody>${(schedule.items || []).map(schedulerRow).join('')}</tbody>
        </table>
      </div>
    </div>
  `;
}
