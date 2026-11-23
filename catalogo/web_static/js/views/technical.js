import { api } from '../api.js';
import { esc, fmtInt } from '../format.js';
import { availabilityStatus } from '../labels.js';
import { navigate } from '../router.js';
import { status as chip } from '../ui.js';
import { $, badge, empty, icon, setHeader, status, thumb } from '../ui.js';

const STATE_LABELS = {
  COLD: ['Frío', 'muted'],
  ACTIVATING: ['Activando', 'info'],
  DOWNLOADING_LOCAL: ['Descargando', 'info'],
  SEEDING_LOCAL: ['Sembrando', 'ok'],
  MOVING_TO_1FICHIER: ['Moviendo', 'warn'],
  ON_1FICHIER: ['En archivo', 'info'],
};

export async function renderTechnical({ query, signal }) {
  const filters = {
    q: query.get('q') || '',
    kind: query.get('kind') || '',
    availability: query.get('availability') || '',
    status: query.get('status') || '',
    sort: query.get('sort') || 'popularity',
    page: Math.max(1, Number(query.get('page')) || 1),
  };
  setHeader('Detalle técnico');
  const params = new URLSearchParams({
    q: filters.q,
    kind: filters.kind,
    availability: filters.availability,
    status: filters.status,
    sort: filters.sort,
    page: String(filters.page),
    per_page: '50',
  });
  const data = await api(`/api/titles?${params}`, { signal });

  const rows = data.items.map((item) => {
    const stateLabel = STATE_LABELS[item.status] || [item.status || '—', 'muted'];
    const av = availabilityStatus(item.availability);
    const tag = item.kind === 'series' ? `T${String(item.season || 0).padStart(2, '0')}E${String(item.episode || 0).padStart(2, '0')}` : '';
    return `<tr>
      <td data-label="Título"><div class="row" style="gap:9px;flex-wrap:nowrap">${thumb(item.poster_path, item.title)}<div class="cell-title"><strong>${esc(item.title || '—')}</strong><small>${esc(tag || item.key)}</small></div></div></td>
      <td data-label="Año" class="num">${item.year || '—'}</td>
      <td data-label="Tipo">${badge(item.kind === 'movie' ? 'película' : 'serie', 'mono')} ${tag ? `<span class="tiny muted">${tag}</span>` : ''}</td>
      <td data-label="Disponibilidad">${status(av.label, av.tone)}</td>
      <td data-label="Releases">${fmtInt(item.releases)} <span class="faint">/ ${fmtInt(item.alive)} vivos</span></td>
      <td data-label="Calidad">${esc(item.quality || '—')}</td>
      <td data-label="Estado">${status(stateLabel[0], stateLabel[1])}</td>
      <td class="actions" data-label="">
        <a class="btn btn-sm btn-ghost" href="#/title/${item.id}">Ficha</a>
        <button class="btn btn-sm btn-ghost" data-action="quick-sync" data-id="${item.id}">Fuentes</button>
      </td>
    </tr>`;
  }).join('');

  $('#view').innerHTML = `
    <div class="notice section" style="margin-top:0">Vista interna.</div>
    <form class="toolbar section" data-form="technical" style="margin-top:12px">
      <label class="field grow"><span>Buscar</span><input name="q" value="${esc(filters.q)}" placeholder="título, título original o clave" autocomplete="off"></label>
      <label class="field"><span>Tipo</span><select name="kind">
        <option value="">Todo</option>
        <option value="movie" ${filters.kind === 'movie' ? 'selected' : ''}>Películas</option>
        <option value="series" ${filters.kind === 'series' ? 'selected' : ''}>Series</option>
      </select></label>
      <label class="field"><span>Disponibilidad</span><select name="availability">
        <option value="">Todo</option>
        ${[['live', 'Con fuente viva'], ['none', 'Sin release'], ['unverified', 'Sin verificar'], ['local', 'Local'], ['cloud', 'archivo'], ['debrid', 'Debrid'], ['library', 'Radarr/Sonarr'], ['downloading', 'Descargando']].map(([value, label]) => `<option value="${value}" ${filters.availability === value ? 'selected' : ''}>${label}</option>`).join('')}
      </select></label>
      <label class="field"><span>Estado</span><select name="status">
        <option value="">Todo</option>
        ${Object.entries(STATE_LABELS).map(([value, [label]]) => `<option value="${value}" ${filters.status === value ? 'selected' : ''}>${label}</option>`).join('')}
      </select></label>
      <label class="field"><span>Orden</span><select name="sort">
        ${[['popularity', 'Popularidad'], ['title', 'Título'], ['year', 'Año'], ['recent', 'Reciente']].map(([value, label]) => `<option value="${value}" ${filters.sort === value ? 'selected' : ''}>${label}</option>`).join('')}
      </select></label>
      <button class="btn btn-primary" type="submit">${icon('filter')} Aplicar</button>
    </form>

    <div class="row between section" style="margin-bottom:9px">
      <span class="tiny faint">${fmtInt(data.total)} títulos, página ${data.page} de ${data.pages}</span>
      <span class="tiny faint">${data.per_page} por página</span>
    </div>

    <div class="panel flush">
      ${rows ? `<div class="table-wrap"><table class="table--stack">
        <thead><tr><th>Título</th><th>Año</th><th>Tipo</th><th>Disponibilidad</th><th>Releases</th><th>Calidad</th><th>Estado</th><th></th></tr></thead>
        <tbody>${rows}</tbody>
      </table></div>` : empty('Sin resultados', 'Prueba otros filtros.')}
    </div>

    <div class="row between section">
      <span class="tiny faint">${fmtInt(data.total)} resultados</span>
      <div class="row" style="gap:6px">
        <button class="btn btn-sm btn-ghost" data-action="technical-page" data-page="${data.page - 1}" ${data.page <= 1 ? 'disabled' : ''}>${icon('arrow-left', 'sm')} Anterior</button>
        <span class="badge mono">${data.page} / ${data.pages}</span>
        <button class="btn btn-sm btn-ghost" data-action="technical-page" data-page="${data.page + 1}" ${data.page >= data.pages ? 'disabled' : ''}>Siguiente ${icon('arrow-right', 'sm')}</button>
      </div>
    </div>
  `;
}
