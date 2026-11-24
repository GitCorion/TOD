import { api } from '../api.js';
import { esc, fmtShortDate } from '../format.js';
import { $, empty, icon, poster, setHeader, status } from '../ui.js';

function tag(item) {
  if (item.kind !== 'series') return 'Película';
  const season = Number(item.season || 0);
  const episode = Number(item.episode || 0);
  return season && episode
    ? `T${String(season).padStart(2, '0')}E${String(episode).padStart(2, '0')}`
    : 'Serie';
}

export async function renderCalendar({ query, signal }) {
  const days = [30, 90, 365].includes(Number(query.get('days')))
    ? Number(query.get('days'))
    : 90;
  const data = await api(`/api/calendar?days=${days}`, { signal });
  const items = data.items || [];
  setHeader('Calendario');

  const groups = new Map();
  for (const item of items) {
    const date = item.release_date || item.air_date;
    if (!groups.has(date)) groups.set(date, []);
    groups.get(date).push(item);
  }

  const sections = [...groups.entries()].map(([date, rows]) => `<div class="panel flush section">
    <div class="panel-head"><h2>${esc(fmtShortDate(`${date}T00:00:00`))}</h2><span class="right tiny faint">${rows.length} ${rows.length === 1 ? 'estreno' : 'estrenos'}</span></div>
    <div class="media-rows">
      ${rows.map((item) => {
        const href = item.kind === 'series' && item.tmdb
          ? `#/series/${item.tmdb}`
          : `#/title/${item.id}`;
        return `<a class="media-row" href="${href}">
          <span class="poster">${poster(item.poster_path, item.title, { size: 'w92' })}</span>
          <div class="info"><strong>${esc(item.title || '—')}</strong><small>${esc(tag(item))}${item.monitored ? ', en seguimiento' : ''}</small></div>
          ${item.published
            ? status('Disponible', 'ok')
            : item.library_added
              ? status('En Radarr/Sonarr', 'info')
              : item.monitored
                ? status('En seguimiento', 'info')
                : '<span class="tiny faint">Sin catalogar</span>'}
          ${icon('chevron', 'sm')}
        </a>`;
      }).join('')}
    </div>
  </div>`).join('');

  $('#view').innerHTML = `
    <div class="row between section">
      <div class="chips" role="tablist" aria-label="Rango del calendario">
        ${[30, 90, 365].map((value) => `<button class="chip ${days === value ? 'active' : ''}" data-action="calendar-days" data-days="${value}" role="tab" aria-selected="${days === value}">${value} días</button>`).join('')}
      </div>
      <span class="tiny faint">${items.length} estrenos o emisiones fechados</span>
    </div>
    ${sections || `<div class="panel pad">${empty('Sin fechas próximas', 'No hay estrenos ni emisiones fechados en este rango.')}</div>`}
  `;
}
