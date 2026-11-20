import { api } from '../api.js';
import { esc, fmtDate } from '../format.js';
import { eventLabel, jobLabel, jobState } from '../labels.js';
import { $, badge, empty, setHeader, status } from '../ui.js';

export async function renderHistory({ signal }) {
  const [jobs, events] = await Promise.all([
    api('/api/jobs?limit=100', { signal }),
    api('/api/events?limit=100', { signal }),
  ]);
  setHeader('Historial', 'Operación');

  const jobRows = (jobs.items || []).map((job) => `<tr>
    <td data-label="Fecha" class="nowrap">${fmtDate(job.finished_at || job.created_at)}</td>
    <td data-label="Tarea"><strong>${esc(jobLabel(job.kind))}</strong><div class="tiny faint">${esc(job.id)}</div></td>
    <td data-label="Estado">${status(jobState(job.state).label, jobState(job.state).tone)}</td>
    <td data-label="Resultado"><span class="tiny">${esc(job.error || job.message || '—')}</span></td>
  </tr>`).join('');

  const eventRows = (events.items || []).map((event) => `<tr>
    <td data-label="Fecha" class="nowrap">${fmtDate(event.ts)}</td>
    <td data-label="Acción">${badge(eventLabel(event.kind), 'mono')}</td>
    <td data-label="Título">${event.asset_id ? `<a href="#/title/${event.asset_id}">#${event.asset_id}</a>` : '—'}</td>
    <td data-label="Datos"><details class="event-payload"><summary>ver datos</summary><pre>${esc(event.payload || '')}</pre></details></td>
  </tr>`).join('');

  $('#view').innerHTML = `
    <div class="panel flush">
      <div class="panel-head"><h2>Tareas</h2><span class="right tiny faint">últimas 100</span></div>
      ${jobRows ? `<div class="table-wrap"><table class="table--stack">
        <thead><tr><th>Fecha</th><th>Tarea</th><th>Estado</th><th>Resultado</th></tr></thead>
        <tbody>${jobRows}</tbody>
      </table></div>` : empty('Sin tareas', 'Todavía no se ha ejecutado ninguna tarea.')}
    </div>

    <div class="panel flush section">
      <div class="panel-head"><h2>Auditoría</h2><span class="right tiny faint">últimos 100 eventos</span></div>
      ${eventRows ? `<div class="table-wrap"><table class="table--stack">
        <thead><tr><th>Fecha</th><th>Acción</th><th>Título</th><th>Datos</th></tr></thead>
        <tbody>${eventRows}</tbody>
      </table></div>` : empty('Sin eventos', 'Las acciones de la interfaz aparecerán aquí.')}
    </div>
  `;
}
