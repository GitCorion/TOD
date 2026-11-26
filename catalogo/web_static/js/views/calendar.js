import { api } from '../api.js';
import { esc, fmtShortDate } from '../format.js';
import { $, empty, icon, poster, setHeader, status } from '../ui.js';

function tag(item) {
  if (item.kind !== 'series') return 'Película';
  const season = Number(item.season || 0);
  const episode = Number(item.episode || 0);
  return season && episode
    ? `S${String(season).padStart(2, '0')}E${String(episode).padStart(2, '0')}`
    : 'Serie';
}

function isoDate(d) {
  return d.toISOString().slice(0, 10);
}

function itemColorClass(item, todayStr) {
  const dt = (item.release_date || item.air_date || '').slice(0, 10);
  if (item.published) return 'cal-ok';
  if (!item.monitored && !item.library_added) return 'cal-unmonitored';
  if (dt && dt <= todayStr) return item.has_alive ? 'cal-info' : 'cal-missing';
  return 'cal-upcoming';
}

function itemStatusBadge(item, todayStr) {
  const dt = (item.release_date || item.air_date || '').slice(0, 10);
  if (item.published) return status('Disponible', 'ok');
  if (item.library_added) return status('En biblioteca', 'info');
  if (item.monitored) {
    if (dt && dt <= todayStr) return item.has_alive ? status('Fuente lista', 'info') : status('Sin fuente', 'warn');
    return status('Sin estrenar', 'info');
  }
  return '<span class="tiny faint">Sin añadir</span>';
}

function weekRange(offset = 0) {
  const now = new Date();
  const day = (now.getDay() + 6) % 7; // lunes = 0
  const start = new Date(now);
  start.setDate(now.getDate() - day + offset * 7);
  const end = new Date(start);
  end.setDate(start.getDate() + 6);
  return { start, end };
}

function monthRange(offset = 0) {
  const now = new Date();
  const start = new Date(now.getFullYear(), now.getMonth() + offset, 1);
  const end = new Date(now.getFullYear(), now.getMonth() + offset + 1, 0);
  return { start, end };
}

export async function renderCalendar({ query, signal }) {
  const mode = ['agenda', 'week', 'month'].includes(query.get('view')) ? query.get('view') : 'agenda';
  const offset = Number(query.get('offset') || 0);
  const days = [30, 90, 365].includes(Number(query.get('days'))) ? Number(query.get('days')) : 90;
  const kind = query.get('kind') || '';
  const monitoredOnly = query.get('monitored') === '1';

  const params = new URLSearchParams();
  let rangeLabel = `${days} días`;
  let gridStart = null;
  let gridEnd = null;
  if (mode === 'week') {
    const r = weekRange(offset);
    gridStart = r.start;
    gridEnd = r.end;
    params.set('start', isoDate(r.start));
    params.set('end', isoDate(r.end));
    rangeLabel = `${isoDate(r.start)} → ${isoDate(r.end)}`;
  } else if (mode === 'month') {
    const r = monthRange(offset);
    gridStart = r.start;
    gridEnd = r.end;
    params.set('start', isoDate(r.start));
    params.set('end', isoDate(r.end));
    rangeLabel = r.start.toLocaleDateString('es-ES', { month: 'long', year: 'numeric' });
  } else {
    params.set('days', String(days));
  }
  if (kind) params.set('kind', kind);
  if (monitoredOnly) params.set('monitored', '1');

  const data = await api(`/api/calendar?${params}`, { signal });
  const items = data.items || [];
  const todayStr = isoDate(new Date());
  setHeader('Calendario');

  const groups = new Map();
  for (const item of items) {
    const date = (item.release_date || item.air_date || '').slice(0, 10);
    if (!date) continue;
    if (!groups.has(date)) groups.set(date, []);
    groups.get(date).push(item);
  }

  let bodyHtml = '';
  if (mode === 'week' || mode === 'month') {
    const cells = [];
    const cur = new Date(gridStart);
    if (mode === 'month') {
      const firstWeekday = (cur.getDay() + 6) % 7;
      for (let i = 0; i < firstWeekday; i += 1) {
        cells.push('<div class="cal-cell cal-pad"></div>');
      }
    }
    while (cur <= gridEnd) {
      const ds = isoDate(cur);
      const dayItems = groups.get(ds) || [];
      const isToday = ds === todayStr;
      cells.push(`<div class="cal-cell ${isToday ? 'is-today' : ''}">
        <div class="cal-day-head">
          <span>${cur.toLocaleDateString('es-ES', { weekday: 'short', day: 'numeric' })}</span>
          ${dayItems.length ? `<small>${dayItems.length}</small>` : ''}
        </div>
        <div class="cal-day-items">
          ${dayItems.map((item) => {
            const href = item.kind === 'series' && item.tmdb ? `#/series/${item.tmdb}` : `#/title/${item.id}`;
            return `<a class="cal-pill ${itemColorClass(item, todayStr)}" href="${href}" title="${esc(item.title || '')} ${esc(tag(item))}">
              <strong>${esc(item.title || '—')}</strong>
              <small>${esc(tag(item))}</small>
            </a>`;
          }).join('')}
        </div>
      </div>`);
      cur.setDate(cur.getDate() + 1);
    }
    bodyHtml = `<div class="cal-grid ${mode === 'week' ? 'cal-week' : 'cal-month'}">${cells.join('')}</div>`;
  } else {
    bodyHtml = [...groups.entries()].map(([date, rows]) => `<div class="panel flush section">
      <div class="panel-head"><h2>${esc(fmtShortDate(`${date}T00:00:00`))}</h2><span class="right tiny faint">${rows.length} ${rows.length === 1 ? 'estreno' : 'estrenos'}</span></div>
      <div class="media-rows">
        ${rows.map((item) => {
          const href = item.kind === 'series' && item.tmdb ? `#/series/${item.tmdb}` : `#/title/${item.id}`;
          return `<a class="media-row" href="${href}">
            <span class="poster">${poster(item.poster_path, item.title, { size: 'w92' })}</span>
            <div class="info"><strong>${esc(item.title || '—')}</strong><small>${esc(tag(item))}${item.episode_title ? ` · ${esc(item.episode_title)}` : ''}${item.monitored ? ', monitorizado' : ''}</small></div>
            ${itemStatusBadge(item, todayStr)}
            ${icon('chevron', 'sm')}
          </a>`;
        }).join('')}
      </div>
    </div>`).join('') || `<div class="panel pad">${empty('Sin fechas próximas', 'No hay estrenos ni emisiones fechados en este rango.')}</div>`;
  }

  $('#view').innerHTML = `
    <div class="row between section" style="flex-wrap:wrap;gap:10px">
      <div class="row" style="gap:8px;flex-wrap:wrap">
        <div class="seg" role="tablist" aria-label="Vista del calendario">
          <button class="seg-item ${mode === 'agenda' ? 'active' : ''}" data-action="catalog-filter" data-key="view" data-value="agenda">Agenda</button>
          <button class="seg-item ${mode === 'week' ? 'active' : ''}" data-action="catalog-filter" data-key="view" data-value="week">Semana</button>
          <button class="seg-item ${mode === 'month' ? 'active' : ''}" data-action="catalog-filter" data-key="view" data-value="month">Mes</button>
        </div>
        ${mode === 'agenda'
          ? `<div class="chips">${[30, 90, 365].map((value) => `<button class="chip ${days === value ? 'active' : ''}" data-action="calendar-days" data-days="${value}">${value} días</button>`).join('')}</div>`
          : `<div class="row" style="gap:4px">
              <button class="btn btn-sm btn-ghost" data-action="catalog-filter" data-key="offset" data-value="${offset - 1}">${icon('arrow-left', 'sm')}</button>
              <button class="btn btn-sm btn-ghost" data-action="catalog-filter" data-key="offset" data-value="0">Hoy</button>
              <button class="btn btn-sm btn-ghost" data-action="catalog-filter" data-key="offset" data-value="${offset + 1}">${icon('arrow-right', 'sm')}</button>
              <strong style="margin-left:6px;text-transform:capitalize">${esc(rangeLabel)}</strong>
            </div>`}
      </div>
      <div class="row" style="gap:8px;flex-wrap:wrap">
        <select class="input input-sm" data-change="calendar-kind" aria-label="Filtrar tipo">
          <option value="" ${kind === '' ? 'selected' : ''}>Todo</option>
          <option value="movie" ${kind === 'movie' ? 'selected' : ''}>Películas</option>
          <option value="series" ${kind === 'series' ? 'selected' : ''}>Series</option>
        </select>
        <label class="inline check tiny">
          <input type="checkbox" data-action="catalog-filter" data-key="monitored" data-value="${monitoredOnly ? '' : '1'}" ${monitoredOnly ? 'checked' : ''}>
          Solo monitorizados
        </label>
        <span class="tiny faint">${items.length} eventos</span>
      </div>
    </div>
    ${bodyHtml}
  `;
}
