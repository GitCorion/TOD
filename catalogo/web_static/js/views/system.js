import { api } from '../api.js';
import { attentionCard, paintAttention } from '../attention.js';
import { esc, fmtBytes, fmtDate, fmtInt, fmtRelative } from '../format.js';
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

const EXTERNAL_CAPABILITY = {
  'qbittorrent.service': 'qbittorrent',
  'prowlarr.service': 'prowlarr',
  'jackett.service': 'jackett',
  'jellyfin.service': 'jellyfin',
};

function externalPanel(items, capabilities, runtime) {
  if (runtime !== 'docker') return servicePanel('Terceros', items);
  return `<div class="panel">
    <div class="panel-head"><h2>Terceros</h2><span class="right tiny faint">Docker Compose</span></div>
    <div class="panel-body" style="padding-top:6px;padding-bottom:8px">
      ${items.map((item) => {
        const key = EXTERNAL_CAPABILITY[item.unit];
        const ready = key ? Boolean(capabilities[key]) : false;
        return `<div class="kv">
          <span title="${esc(item.unit)}">${esc(serviceName(item.unit))}</span>
          <span>${ready ? status('Configurado', 'ok') : status('No configurado', 'muted')}</span>
        </div>`;
      }).join('')}
      <p class="tiny faint" style="margin:8px 0 0">Los procesos se gestionan con <span class="mono">docker compose</span>. Aquí se muestra si cada integración está configurada.</p>
    </div>
  </div>`;
}

function healthAge(value) {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return (Date.now() - date.getTime()) / 3600000;
}

function timerLine(label, timer, last, expectedHours) {
  const active = timer?.active === 'active';
  const age = healthAge(last?.finished_at || last?.created_at);
  const stale = expectedHours && age !== null && age > expectedHours;
  const tone = active && !stale ? 'ok' : stale ? 'warn' : 'bad';
  const text = active ? (stale ? 'Activo, con retraso' : 'Activo') : 'Parado';
  return `<div class="kv"><span>${esc(label)}</span><span>${status(text, tone)}</span></div>`;
}

function jobSummary(job, keys) {
  if (!job) return '<span class="faint">Sin ejecutar</span>';
  const result = job.result || {};
  const pieces = keys
    .filter(([name]) => result[name] !== undefined)
    .map(([name, one, many]) => {
      const value = Number(result[name]) || 0;
      return `${fmtInt(value)} ${value === 1 ? one : many}`;
    });
  const when = fmtRelative(job.finished_at || job.created_at);
  const stateLabel = job.state === 'error' ? 'error' : pieces.length ? pieces.join(', ') : 'sin cambios';
  return `${esc(when)}, ${esc(stateLabel)}`;
}

function indexerRows(indexers) {
  if (!indexers.length) return empty('Sin indexers', 'Prowlarr no devolvió datos.');
  return indexers.map((indexer) => `<div class="kv">
    <span>${esc(indexer.name)} ${indexer.enabled === false ? status('Pausado', 'warn') : ''}</span>
    <span class="row" style="gap:8px">
      <span class="tiny faint">${fmtInt(indexer.queries)} consultas</span>
      ${indexer.failed
        ? status(`${fmtInt(indexer.failed)} ${indexer.failed === 1 ? 'fallo' : 'fallos'}`, 'warn')
        : status('Sin fallos', 'ok')}
      <span class="tiny faint">${fmtInt(indexer.response_ms)} ms</span>
    </span>
  </div>`).join('');
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
  const [system, events, attention, health] = await Promise.all([
    api('/api/system', { signal }),
    api('/api/events?limit=60', { signal }),
    api('/api/attention', { signal }).catch(() => null),
    api('/api/monitor/health', { signal }).catch(() => null),
  ]);
  state.system = system;
  state.attention = attention;
  paintAttention(attention);
  setHeader('Estado');

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

  const rss = health?.last_rss;
  const monitor = health?.last_monitor;
  const rssAge = healthAge(rss?.finished_at || rss?.created_at);
  const monitorAge = healthAge(monitor?.finished_at || monitor?.created_at);
  const rssWarning = rssAge !== null && rssAge > 2;
  const monitorWarning = monitorAge !== null && monitorAge > 25;
  const timersOk = health?.timers?.rss?.active === 'active'
    && health?.timers?.monitor?.active === 'active';
  const healthLabel = !health ? 'Sin datos' : !timersOk ? 'Parado' : rssWarning || monitorWarning ? 'Revisar' : 'Al día';
  const healthTone = !health ? 'muted' : !timersOk ? 'bad' : rssWarning || monitorWarning ? 'warn' : 'ok';

  const eventGroups = [];
  for (const event of events.items || []) {
    const ts = new Date(event.ts).getTime();
    const last = eventGroups[eventGroups.length - 1];
    if (
      last
      && last.kind === event.kind
      && last.asset_id === event.asset_id
      && Math.abs(last.lastTs - ts) < 5 * 60 * 1000
    ) {
      last.count += 1;
      last.payloads.push(event.payload);
      last.firstTs = Math.min(last.firstTs, ts);
      last.lastTs = Math.max(last.lastTs, ts);
    } else {
      eventGroups.push({
        ...event,
        count: 1,
        payloads: [event.payload],
        firstTs: ts,
        lastTs: ts,
      });
    }
  }
  const eventRows = eventGroups.map((event) => `<tr>
    <td data-label="Fecha" class="nowrap">${fmtDate(event.ts)}</td>
    <td data-label="Acción">${badge(eventLabel(event.kind), 'mono')}${event.count > 1 ? ` <span class="tiny faint">×${event.count}</span>` : ''}</td>
    <td data-label="Título">${event.asset_id ? `<a href="#/title/${event.asset_id}">Abrir ficha</a>` : '—'}</td>
    <td data-label="Datos"><details class="event-payload"><summary>ver datos</summary><pre>${event.payloads.map((payload) => esc(payload || '')).join('\n\n')}</pre></details></td>
  </tr>`).join('');

  $('#view').innerHTML = `
    <div class="section" style="margin-top:0">${attentionCard(attention, { compact: false })}</div>

    <div class="grid grid-2 section">
      ${servicePanel('Aplicación', apps)}
      ${externalPanel(external, system.capabilities || {}, system.runtime)}
    </div>

    <div class="grid grid-2 section">
      ${schedulerPanel(system.scheduler)}
      <div class="panel">
        <div class="panel-head"><h2>Integraciones y base</h2></div>
        <div class="panel-body" style="padding-top:6px;padding-bottom:8px">
          ${integrations}
          <div class="kv"><span>Trackers</span><span>${(system.trackers || []).filter((item) => item.enabled !== 0).length} activos · ${(system.trackers || []).filter((item) => item.enabled === 0).length} bloqueados · <a class="tiny" href="#/settings?section=siembra">Gestionar</a></span></div>
          <div class="kv"><span>qBittorrent</span><span>${system.qbit?.ok ? `${esc(system.qbit.version)}, ${fmtInt(system.qbit.torrents)} torrents` : `<span class="error">${esc(system.qbit?.error || 'sin conexión')}</span>`}</span></div>
          <div class="kv"><span>Base de datos</span><span class="mono tiny">${esc(system.db?.path || '—')}, ${fmtBytes(system.db?.size || 0)}</span></div>
          <div class="kv"><span>Títulos / versiones</span><span>${fmtInt(system.db?.media_items || 0)} / ${fmtInt(system.db?.releases || 0)}</span></div>
          <div class="kv"><span>Reproductor</span><span>${system.capabilities?.play_service ? status('Activo', 'ok') : status(system.play_error || 'Inactivo', 'muted')}</span></div>
          <div class="kv"><span>Acceso</span><span>${system.auth_required ? 'Token obligatorio' : 'LAN / WireGuard'}</span></div>
          <div class="kv"><span>Detalle interno</span><a class="tiny" href="#/technical">Abrir detalle técnico</a></div>
        </div>
      </div>
    </div>

    <div class="grid grid-2 section">
      <div class="panel">
        <div class="panel-head"><h2>Seguimiento y RSS</h2>${status(healthLabel, healthTone)}</div>
        <div class="panel-body" style="padding-top:6px;padding-bottom:8px">
          ${timerLine('RSS', health?.timers?.rss, rss, 2)}
          <div class="kv"><span>Última ingesta</span><span>${jobSummary(rss, [['fetched', 'leído', 'leídos'], ['inserted', 'nuevo', 'nuevos'], ['exported_keys', 'añadido', 'añadidos']])}</span></div>
          <div class="kv"><span>Seguimientos cerrados</span><span>${rss ? fmtInt(rss.result?.unmonitored_keys || 0) : '—'}</span></div>
          ${timerLine('Barrido diario', health?.timers?.monitor, monitor, 25)}
          <div class="kv"><span>Último barrido</span><span>${jobSummary(monitor, [['checked', 'revisado', 'revisados'], ['found', 'encontrado', 'encontrados'], ['waiting', 'esperando', 'esperando']])}</span></div>
          ${monitorAge !== null && monitorAge > 25 ? '<div class="notice">El barrido diario lleva más de 25 horas sin ejecutarse.</div>' : ''}
          ${rssAge !== null && rssAge > 2 ? '<div class="notice">El RSS lleva más de 2 horas sin ejecutarse.</div>' : ''}
        </div>
      </div>
      <div class="panel">
        <div class="panel-head"><h2>Indexers</h2><span class="right tiny faint">${health?.indexers?.length || 0} con actividad · <a href="#/settings?section=indexers">Configurar</a></span></div>
        <div class="panel-body" style="padding-top:6px;padding-bottom:8px">
          ${health?.prowlarr_error ? `<div class="notice">${esc(health.prowlarr_error)}</div>` : indexerRows(health?.indexers || [])}
        </div>
      </div>
    </div>

    <div class="panel section">
      <div class="panel-head">
        <h2>Discos y rutas</h2>
        <div class="right row" style="gap:6px">
          <button class="btn btn-sm btn-ghost" data-action="scheduler-run" data-task="arr-mirror">Espejo Radarr/Sonarr</button>
          <button class="btn btn-sm btn-ghost" data-action="scheduler-run" data-task="restore-strm">Reconciliar .strm</button>
        </div>
      </div>
      <div class="panel-body">
        ${disks || empty('Sin rutas', 'No se pudieron leer los discos.')}
        <div class="divider"></div>
        <div class="kv"><span>Carpeta de los .strm</span><span class="mono">${esc(system.paths?.catalog_library || '—')}</span></div>
        <div class="kv"><span>Semillas</span><span class="mono">${esc(system.paths?.seed_root || '—')}</span></div>
        ${system.paths?.library_root_movies ? `<div class="kv"><span>Carpeta de películas</span><span class="mono">${esc(system.paths.library_root_movies)}</span></div>` : ''}
        ${system.paths?.library_root_series ? `<div class="kv"><span>Carpeta de series</span><span class="mono">${esc(system.paths.library_root_series)}</span></div>` : ''}
        ${!system.paths?.library_root_movies && !system.paths?.library_root_series ? `<div class="kv"><span>Carpeta común</span><span class="mono">${esc(system.paths?.library_root || '—')}</span></div>` : ''}
        <div class="kv"><span>Carpetas .strm</span><span class="mono">${esc(system.paths?.strm_roots || '—')} <a class="tiny" href="#/settings">Cambiar</a></span></div>
      </div>
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
