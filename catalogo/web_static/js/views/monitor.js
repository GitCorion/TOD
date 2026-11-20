import { api } from '../api.js';
import { esc, fmtInt, fmtRelative, fmtShortDate, pct } from '../format.js';
import { updateQuery } from '../router.js';
import { state } from '../store.js';
import { $, badge, bar, empty, icon, setHeader, status, thumb } from '../ui.js';

function stateInfo(item) {
  switch (item.state) {
    case 'pending':
      return status('Pendiente de estreno', 'muted');
    case 'downloading':
      return `${status('Descargando', 'info')} <span class="tiny faint">${pct(item.progress)}%</span>`;
    case 'queued':
      return status('En cola', 'warn');
    case 'waiting':
      return status('Por buscar', 'warn');
    default:
      return status('Sin fuente', 'muted');
  }
}

function availability(item) {
  if (item.available !== false) return '<span class="tiny faint">Disponible</span>';
  return `<span class="tiny faint">${esc(fmtShortDate(item.available_at))}</span>`;
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
  if (!indexers.length) return '<div class="empty"><strong>Sin estadísticas</strong>Prowlarr no devolvió datos de indexers.</div>';
  return indexers.map((indexer) => `<div class="kv">
    <span>${esc(indexer.name)} ${indexer.enabled === false ? status('Pausado', 'warn') : ''}</span>
    <span class="row" style="gap:8px">
      <span class="tiny faint">${fmtInt(indexer.queries)} consultas</span>
      ${indexer.failed
        ? status(`${fmtInt(indexer.failed)} fallos`, 'warn')
        : status('Sin fallos', 'ok')}
      <span class="tiny faint">${fmtInt(indexer.response_ms)} ms</span>
      <button class="btn btn-sm btn-ghost" data-action="monitor-indexer" data-id="${indexer.id}" data-enabled="${indexer.enabled === false ? '0' : '1'}">${indexer.enabled === false ? 'Activar' : 'Pausar'}</button>
    </span>
  </div>`).join('');
}

export async function renderMonitor({ query, signal }) {
  const filters = {
    q: query.get('q') || '',
    state: query.get('state') || '',
    kind: query.get('kind') || '',
    page: Math.max(1, Number(query.get('page')) || 1),
  };
  const params = new URLSearchParams({
    q: filters.q,
    state: filters.state,
    kind: filters.kind,
    page: String(filters.page),
    per_page: '50',
  });
  const data = await api(`/api/monitor?${params}`, { signal });
  const items = data.items || [];
  const summary = data.summary || {};
  const health = data.health || {};
  state.monitorSummary = summary;
  setHeader('Seguimiento', 'Biblioteca');

  const chips = [
    ['', 'Todos', summary.total || 0],
    ['missing', 'Sin fuente', summary.missing || 0],
    ['pending', 'Pendiente de estreno', summary.pending || 0],
    ['downloading', 'Descargando', summary.downloading || 0],
  ];

  const rows = items.map((item) => {
    const label = item.kind === 'series'
      ? `T${String(item.season || 0).padStart(2, '0')}E${String(item.episode || 0).padStart(2, '0')}`
      : 'Película';
    const href = item.kind === 'series'
      ? `#/series/${item.tmdb}`
      : `#/title/${item.id}`;
    return `<tr>
      <td data-label="Título">
        <div class="episode-title-cell">
          ${thumb(item.poster_path, item.title)}
          <div class="cell-title">
            <strong>${esc(item.title || '—')}</strong>
            <small>${esc(item.year || '—')}, ${esc(label)}</small>
          </div>
        </div>
      </td>
      <td data-label="Estado">${stateInfo(item)}</td>
      <td data-label="Disponibilidad">${availability(item)}</td>
      <td data-label="Fuentes">${item.releases ? `${fmtInt(item.releases)} <span class="faint">/ ${fmtInt(item.alive)}</span>` : '<span class="faint">—</span>'}</td>
      <td data-label="Última búsqueda">${item.last_search ? esc(fmtRelative(item.last_search)) : '<span class="faint">Nunca</span>'}</td>
      <td class="actions" data-label="">
        <button class="btn btn-sm btn-ghost" data-action="monitor-search" data-key="${esc(item.key)}">${icon('search', 'sm')} Buscar ahora</button>
        <button class="btn btn-sm btn-ghost" data-action="unmonitor-title" data-id="${item.id}">Quitar</button>
        <a class="btn btn-sm btn-ghost" href="${href}">Ficha</a>
      </td>
    </tr>`;
  }).join('');

  const rss = health.last_rss;
  const monitor = health.last_monitor;
  const rssAge = healthAge(rss?.finished_at || rss?.created_at);
  const monitorAge = healthAge(monitor?.finished_at || monitor?.created_at);
  const rssWarning = rssAge !== null && rssAge > 2;
  const monitorWarning = monitorAge !== null && monitorAge > 25;
  const timersOk = health.timers?.rss?.active === 'active'
    && health.timers?.monitor?.active === 'active';
  const healthLabel = !timersOk ? 'Parado' : rssWarning || monitorWarning ? 'Revisar' : 'Al día';
  const healthTone = !timersOk ? 'bad' : rssWarning || monitorWarning ? 'warn' : 'ok';

  $('#view').innerHTML = `
    <div class="breadcrumb"><a href="#/catalog">Catálogo</a>${icon('chevron')}<span>Seguimiento</span></div>

    <div class="toolbar">
      <label class="field grow">
        <span>Buscar</span>
        <input data-input="monitor-q" value="${esc(filters.q)}" placeholder="Título, serie o clave" autocomplete="off" aria-label="Buscar en seguimiento">
      </label>
      <label class="field">
        <span>Tipo</span>
        <select data-change="monitor-kind" aria-label="Filtrar por tipo">
          <option value="" ${filters.kind === '' ? 'selected' : ''}>Todo</option>
          <option value="movie" ${filters.kind === 'movie' ? 'selected' : ''}>Películas</option>
          <option value="series" ${filters.kind === 'series' ? 'selected' : ''}>Series</option>
        </select>
      </label>
      <button class="btn btn-primary" data-action="monitor-run">${icon('search')} Buscar disponibles</button>
      <button class="btn btn-icon" data-action="monitor-refresh" aria-label="Actualizar" title="Actualizar">${icon('refresh')}</button>
    </div>

    <div class="panel section" style="padding:12px 14px">
      <div class="row between">
        <div class="chips" role="tablist" aria-label="Estado del seguimiento">
          ${chips.map(([value, label, count]) => `<button class="chip ${filters.state === value ? 'active' : ''}" data-action="monitor-filter" data-value="${value}" role="tab" aria-selected="${filters.state === value}">${esc(label)} <b>${fmtInt(count)}</b></button>`).join('')}
        </div>
        <span class="tiny faint">${fmtInt(summary.total || 0)} en seguimiento</span>
      </div>
    </div>

    <div class="grid grid-2 section">
      <div class="panel">
        <div class="panel-head"><h2>RSS y seguimiento</h2>${status(healthLabel, healthTone)}</div>
        <div class="panel-body" style="padding-top:6px;padding-bottom:8px">
          ${timerLine('RSS', health.timers?.rss, rss, 2)}
          <div class="kv"><span>Última ingesta</span><span>${jobSummary(rss, [['fetched', 'leído', 'leídos'], ['inserted', 'nuevo', 'nuevos'], ['exported_keys', 'publicado', 'publicados']])}</span></div>
          <div class="kv"><span>Seguimientos cerrados</span><span>${rss ? fmtInt(rss.result?.unmonitored_keys || 0) : '—'}</span></div>
          ${timerLine('Barrido diario', health.timers?.monitor, monitor, 25)}
          <div class="kv"><span>Último barrido</span><span>${jobSummary(monitor, [['checked', 'revisado', 'revisados'], ['found', 'encontrado', 'encontrados'], ['waiting', 'esperando', 'esperando']])}</span></div>
          ${monitorAge !== null && monitorAge > 25 ? `<div class="notice">El barrido diario lleva más de 25 horas sin ejecutarse.</div>` : ''}
          ${rssAge !== null && rssAge > 2 ? `<div class="notice">El RSS lleva más de 2 horas sin ejecutarse.</div>` : ''}
        </div>
      </div>
      <div class="panel">
        <div class="panel-head"><h2>Indexers</h2><span class="right tiny faint">${health.indexers?.length || 0} con actividad</span></div>
        <div class="panel-body" style="padding-top:6px;padding-bottom:8px">
          ${health.prowlarr_error ? `<div class="notice">${esc(health.prowlarr_error)}</div>` : indexerRows(health.indexers || [])}
        </div>
      </div>
    </div>

    <div class="panel flush section">
      <div class="panel-head">
        <h2>Cola de seguimiento</h2>
        <span class="right tiny faint">RSS cada 30 min, barrido diario 04:20</span>
      </div>
      ${rows ? `<div class="table-wrap"><table class="table--stack">
        <thead><tr><th>Título</th><th>Estado</th><th>Disponibilidad</th><th>Fuentes</th><th>Última búsqueda</th><th></th></tr></thead>
        <tbody>${rows}</tbody>
      </table></div>` : `<div class="pad">${empty(
        filters.q || filters.state || filters.kind ? 'Sin resultados con esos filtros' : 'No hay títulos en seguimiento',
        'Los títulos monitorizados sin .strm aparecen aquí hasta que se encuentra una fuente.',
      )}</div>`}
    </div>

    <div class="row between section">
      <span class="tiny faint">${fmtInt(data.total || 0)} resultados</span>
      <div class="row" style="gap:6px">
        <button class="btn btn-sm btn-ghost" data-action="monitor-page" data-page="${data.page - 1}" ${data.page <= 1 ? 'disabled' : ''}>${icon('arrow-left', 'sm')} Anterior</button>
        <span class="badge mono">${data.page} / ${data.pages}</span>
        <button class="btn btn-sm btn-ghost" data-action="monitor-page" data-page="${data.page + 1}" ${data.page >= data.pages ? 'disabled' : ''}>Siguiente ${icon('arrow-right', 'sm')}</button>
      </div>
    </div>
  `;
}
