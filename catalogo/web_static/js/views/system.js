import { api } from '../api.js';
import { esc, fmtBytes, fmtDate, fmtInt } from '../format.js';
import { capabilityLabel, eventLabel, SERVICE_LABELS } from '../labels.js';
import { state } from '../store.js';
import { $, badge, barPercent, empty, icon, setHeader, status } from '../ui.js';

function serviceName(unit) {
  const base = unit.replace(/\.(service|timer)$/, '');
  return SERVICE_LABELS[base] || base;
}

function servicePanel(title, items) {
  const label = (value) => value === 'active' ? 'activo' : value === 'inactive' ? 'inactivo' : value;
  return `<div class="panel">
    <div class="panel-head"><h2>${esc(title)}</h2><span class="right tiny faint">${items.filter((item) => item.active === 'active').length}/${items.length} activos</span></div>
    <div class="panel-body" style="padding-top:8px">
      <div class="service-grid">
        ${items.map((item) => `<div class="service">
          <span title="${esc(item.unit)}">${esc(serviceName(item.unit))}</span>
          ${status(label(item.active), item.active === 'active' ? 'ok' : 'bad')}
        </div>`).join('')}
      </div>
    </div>
  </div>`;
}

function schedulerPanel(tasks) {
  const items = tasks || [];
  return `<div class="panel">
    <div class="panel-head"><h2>Tareas programadas</h2><span class="right tiny faint">${items.filter((task) => task.enabled).length}/${items.length} activas</span></div>
    <div class="panel-body" style="padding-top:4px;padding-bottom:8px">
      ${items.map((task) => `<div class="kv">
        <span>${esc(task.label)} ${task.enabled ? status('Activa', 'ok') : status('Pausada', 'muted')}</span>
        <span class="tiny faint">${task.next_run ? `Próxima ${fmtDate(task.next_run)}` : 'Sin programar'}</span>
      </div>`).join('') || empty('Sin tareas', 'No hay tareas programadas configuradas.')}
    </div>
  </div>`;
}

export async function renderSystem({ signal }) {
  const [system, events] = await Promise.all([
    api('/api/system', { signal }),
    api('/api/events?limit=60', { signal }),
  ]);
  state.system = system;
  setHeader('Estado', 'Sistema');

  const services = system.services || [];
  const apps = services.filter((service) => !['qbittorrent.service', 'prowlarr.service', 'jackett.service', 'jellyfin.service'].includes(service.unit));
  const external = services.filter((service) => ['qbittorrent.service', 'prowlarr.service', 'jackett.service', 'jellyfin.service'].includes(service.unit));

  const integrations = Object.entries(system.capabilities || {})
    .filter(([name]) => !['qbittorrent', 'play_service'].includes(name))
    .map(([name, ok]) => `
    <div class="kv"><span>${esc(capabilityLabel(name))}</span><span>${ok ? status('Disponible', 'ok') : status('No configurado', 'muted')}</span></div>
  `).join('');

  const disks = (system.disk || []).map((disk) => {
    if (!disk.ok) {
      return `<div class="disk-row"><div class="disk-head"><span class="mono">${esc(disk.path)}</span><span class="error">sin acceso</span></div></div>`;
    }
    const used = Number(disk.used_pct || 0);
    return `<div class="disk-row">
      <div class="disk-head"><span class="mono">${esc(disk.path)}</span><span>${fmtBytes(disk.free)} libres de ${fmtBytes(disk.total)}, ${used.toFixed(0)} % usado</span></div>
      ${barPercent(used, used >= 92 ? 'bad' : used >= 80 ? 'warn' : 'green')}
    </div>`;
  }).join('');

  const trackers = (system.trackers || []).map((tracker) => `<tr>
    <td data-label="Tracker"><strong>${esc(tracker.tracker)}</strong></td>
    <td data-label="Releases" class="num">${fmtInt(tracker.releases)}</td>
    <td data-label="Vivos" class="num">${fmtInt(tracker.alive)}</td>
    <td data-label="Bloqueados" class="num">${fmtInt(tracker.blocked)}</td>
    <td data-label="Privacidad">${tracker.privacy ? badge(tracker.privacy, 'mono') : '—'}</td>
    <td data-label="Política">${tracker.enabled === 0 ? status('Deshabilitada', 'bad') : status('Activa', 'ok')}</td>
    <td data-label="Seed">${tracker.min_seed_hours ?? '—'} h, ratio ${tracker.target_ratio ?? '—'}</td>
    <td data-label="Última">${fmtDate(tracker.last_checked)}</td>
    <td class="actions" data-label="">
      <button class="btn btn-sm ${tracker.enabled === 0 ? '' : 'btn-danger'}" data-action="tracker-block" data-tracker="${esc(tracker.tracker)}" data-blocked="${tracker.enabled === 0 ? '0' : '1'}">
        ${tracker.enabled === 0 ? 'Desbloquear' : 'Bloquear'}
      </button>
    </td>
  </tr>`).join('');

  const eventRows = (events.items || []).map((event) => `<tr>
    <td data-label="Fecha" class="nowrap">${fmtDate(event.ts)}</td>
    <td data-label="Acción">${badge(eventLabel(event.kind), 'mono')}</td>
    <td data-label="Título">${event.asset_id ? `<a href="#/title/${event.asset_id}">#${event.asset_id}</a>` : '—'}</td>
    <td data-label="Datos"><details class="event-payload"><summary>ver datos</summary><pre>${esc(event.payload || '')}</pre></details></td>
  </tr>`).join('');

  $('#view').innerHTML = `
    <div class="grid grid-2">
      ${servicePanel('Aplicación', apps)}
      ${servicePanel('Terceros', external)}
    </div>

    <div class="grid grid-2 section">
      ${schedulerPanel(system.scheduler)}
      <div class="panel">
        <div class="panel-head"><h2>Integraciones y base</h2></div>
        <div class="panel-body" style="padding-top:6px;padding-bottom:8px">
          ${integrations}
          <div class="kv"><span>qBittorrent</span><span>${system.qbit?.ok ? `${esc(system.qbit.version)}, ${fmtInt(system.qbit.torrents)} torrents` : `<span class="error">${esc(system.qbit?.error || 'sin conexión')}</span>`}</span></div>
          <div class="kv"><span>Base de datos</span><span class="mono tiny">${esc(system.db?.path || '—')}, ${fmtBytes(system.db?.size || 0)}</span></div>
          <div class="kv"><span>Títulos / releases</span><span>${fmtInt(system.db?.media_items || 0)} / ${fmtInt(system.db?.releases || 0)}</span></div>
          <div class="kv"><span>Reproductor</span><span>${system.capabilities?.play_service ? status('Activo', 'ok') : status(system.play_error || 'Inactivo', 'muted')}</span></div>
          <div class="kv"><span>Acceso</span><span>${system.auth_required ? 'Token obligatorio' : 'LAN / WireGuard'}</span></div>
        </div>
      </div>
    </div>

    <div class="panel section">
      <div class="panel-head"><h2>Discos y rutas</h2></div>
      <div class="panel-body">
        ${disks || empty('Sin rutas', 'No se pudieron leer los discos.')}
        <div class="divider"></div>
        <div class="kv"><span>Biblioteca del catálogo</span><span class="mono">${esc(system.paths?.catalog_library || '—')}</span></div>
        <div class="kv"><span>Semillas</span><span class="mono">${esc(system.paths?.seed_root || '—')}</span></div>
        <div class="kv"><span>Raíz de biblioteca</span><span class="mono">${esc(system.paths?.library_root || '—')}</span></div>
        <div class="kv"><span>Raíces .strm</span><span class="mono">${esc(system.paths?.strm_roots || '—')} <a class="tiny" href="#/settings">Cambiar</a></span></div>
      </div>
    </div>

    <div class="panel flush section">
      <div class="panel-head"><h2>Trackers y políticas</h2><span class="right tiny faint">${system.trackers?.length || 0} indexers</span></div>
      ${trackers ? `<div class="table-wrap"><table class="table--stack">
        <thead><tr><th>Tracker</th><th>Releases</th><th>Vivos</th><th>Bloqueados</th><th>Privacidad</th><th>Política</th><th>Seed</th><th>Última</th><th></th></tr></thead>
        <tbody>${trackers}</tbody>
      </table></div>` : empty('Sin trackers indexados')}
    </div>

    <div class="panel flush section">
      <div class="panel-head"><h2>Auditoría</h2><span class="right tiny faint">últimos 60 eventos</span></div>
      ${eventRows ? `<div class="table-wrap"><table class="table--stack">
        <thead><tr><th>Fecha</th><th>Acción</th><th>Título</th><th>Datos</th></tr></thead>
        <tbody>${eventRows}</tbody>
      </table></div>` : empty('Sin eventos')}
    </div>
  `;
}
