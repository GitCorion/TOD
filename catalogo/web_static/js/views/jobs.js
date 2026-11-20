import { api } from '../api.js';
import { esc, fmtDuration, fmtRelative, fmtDate } from '../format.js';
import { jobLabel, jobState, taskDef, TASK_CATEGORIES, TASKS } from '../labels.js';
import { state } from '../store.js';
import { $, badge, bar, empty, icon, setHeader, status } from '../ui.js';

function paramsSummary(job) {
  const params = job.params || {};
  const parts = [];
  if (params.keys) parts.push(`${params.keys.length} claves`);
  if (params.limit) parts.push(`límite ${params.limit}`);
  if (params.year) parts.push(`año ${params.year}`);
  if (params.kind) parts.push(params.kind === 'series' ? 'series' : 'películas');
  if (params.dry_run) parts.push('dry-run');
  if (params.execute) parts.push('ejecución real');
  return parts.join(', ');
}

export async function renderJobs({ signal }) {
  const data = await api('/api/jobs?limit=60', { signal });
  const jobs = data.items || [];
  const active = jobs.find((job) => job.state === 'running') || jobs.find((job) => job.state === 'queued');
  state.jobs = jobs;
  setHeader('Tareas', 'Operación');

  const taskGroups = TASK_CATEGORIES.map((category) => {
    const tasks = TASKS.filter((task) => task.category === category.key);
    if (!tasks.length) return '';
    return `<div class="task-group">
      <div class="section-head"><h2>${esc(category.label)}</h2><span>${tasks.length} tareas</span></div>
      <div class="task-grid">
        ${tasks.map((task) => `<div class="task">
          <h3>${task.danger ? '<span class="danger-dot" title="Puede modificar o borrar datos"></span>' : ''}${esc(task.title)}</h3>
          <p>${esc(task.desc)}</p>
          <div class="foot"><button class="btn btn-sm btn-ghost" data-action="open-task" data-kind="${task.kind}">${icon('play', 'sm')} Configurar</button>${task.danger ? '<span class="tiny faint">revisa el dry-run</span>' : ''}</div>
        </div>`).join('')}
      </div>
    </div>`;
  }).join('');

  const rows = jobs.map((job) => `<tr>
    <td data-label="Estado">${status(jobState(job.state).label, jobState(job.state).tone)}</td>
    <td data-label="Tarea"><div class="cell-title"><strong>${esc(jobLabel(job.kind))}</strong><small>#${job.id}, ${esc(paramsSummary(job) || job.kind)}</small></div></td>
    <td data-label="Mensaje">${esc(job.message || '—')}</td>
    <td data-label="Duración">${fmtDuration(job.started_at, job.finished_at)}</td>
    <td data-label="Cuándo">${esc(fmtRelative(job.finished_at || job.created_at))}</td>
    <td class="actions" data-label="">
      <button class="btn btn-sm btn-ghost" data-action="job-detail" data-id="${job.id}">Detalle</button>
      ${job.state === 'running' || job.state === 'queued' ? `<button class="btn btn-sm btn-danger" data-action="job-cancel" data-id="${job.id}">Cancelar</button>` : ''}
    </td>
  </tr>`).join('');

  $('#view').innerHTML = `
    ${active ? `<div class="panel section">
      <div class="panel-head"><h2>Tarea en curso</h2>${status(active.state === 'running' ? 'Ejecutando' : 'En cola', active.state === 'running' ? 'info' : 'warn')}</div>
      <div class="job-active">
        ${active.state === 'running' ? '<span class="spinner" aria-hidden="true"></span>' : icon('clock')}
        <div class="info"><strong>${esc(jobLabel(active.kind))} <span class="faint" style="font-weight:400">#${active.id}</span></strong><small>${esc(active.message || 'Preparando…')}</small></div>
        ${bar(active.progress || 0)}
        <span class="badge mono">${Math.round((active.progress || 0) * 100)}%</span>
        <button class="btn btn-sm btn-danger" data-action="job-cancel" data-id="${active.id}">Cancelar</button>
      </div>
    </div>` : ''}

    <div class="section">
      <div class="section-head"><h2>Lanzar tarea</h2><span>las marcadas en rojo pueden modificar datos</span></div>
      ${taskGroups}
    </div>

    <div class="panel flush section">
      <div class="panel-head"><h2>Historial</h2><span class="right tiny faint">${jobs.length} trabajos recientes</span></div>
      ${rows ? `<div class="table-wrap"><table class="table--stack">
        <thead><tr><th>Estado</th><th>Tarea</th><th>Mensaje</th><th>Duración</th><th>Cuándo</th><th></th></tr></thead>
        <tbody>${rows}</tbody>
      </table></div>` : empty('Sin tareas', 'Lanza una tarea desde las tarjetas de arriba.')}
    </div>
  `;
}
