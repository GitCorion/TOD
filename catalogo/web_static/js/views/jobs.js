import { api } from '../api.js';
import { esc, fmtDuration, fmtRelative } from '../format.js';
import { jobLabel, jobState, TASKS } from '../labels.js';
import { state } from '../store.js';
import { $, bar, empty, icon, setHeader, status } from '../ui.js';

const QUICK_KINDS = new Set([
  'sync-library',
  'metadata',
  'monitor',
  'rss-sync',
  'qbit-sync',
  'arr-mirror',
]);

function paramsSummary(job) {
  const params = job.params || {};
  const parts = [];
  if (params.keys) parts.push(`${params.keys.length} claves`);
  if (params.limit) parts.push(`límite ${params.limit}`);
  if (params.year) parts.push(`año ${params.year}`);
  if (params.kind) parts.push(params.kind === 'series' ? 'series' : 'películas');
  if (params.dry_run) parts.push('simulación');
  if (params.execute) parts.push('aplicar cambios');
  return parts.join(', ');
}

function taskCard(task) {
  return `<div class="task">
    <h3>${task.danger ? '<span class="danger-dot" title="Puede modificar o borrar datos"></span>' : ''}${esc(task.title)}</h3>
    <p>${esc(task.desc)}</p>
    <div class="foot">
      <button class="btn btn-sm btn-ghost" data-action="open-task" data-kind="${task.kind}">${icon('play', 'sm')} Ejecutar</button>
      ${task.danger ? '<span class="tiny faint">revisar simulación</span>' : ''}
    </div>
  </div>`;
}

export async function renderJobs({ signal }) {
  const data = await api('/api/jobs?limit=60', { signal });
  const jobs = data.items || [];
  const active = jobs.find((job) => job.state === 'running') || jobs.find((job) => job.state === 'queued');
  state.jobs = jobs;
  setHeader('Tareas');

  const quickTasks = TASKS.filter((task) => QUICK_KINDS.has(task.kind));
  const advancedTasks = TASKS.filter((task) => !QUICK_KINDS.has(task.kind));

  const rows = jobs.map((job) => `<tr>
    <td data-label="Estado">${status(jobState(job.state).label, jobState(job.state).tone)}</td>
    <td data-label="Tarea"><div class="cell-title"><strong>${esc(jobLabel(job.kind))}</strong><small>#${job.id}${paramsSummary(job) ? `, ${esc(paramsSummary(job))}` : ''}</small></div></td>
    <td data-label="Mensaje">${esc(job.message || '—')}</td>
    <td data-label="Duración">${fmtDuration(job.started_at, job.finished_at)}</td>
    <td data-label="Cuándo">${esc(fmtRelative(job.finished_at || job.created_at))}</td>
    <td class="actions" data-label="">
      <button class="btn btn-sm btn-ghost" data-action="job-detail" data-id="${job.id}">Detalle</button>
      ${job.state === 'running' || job.state === 'queued' ? `<button class="btn btn-sm btn-danger" data-action="job-cancel" data-id="${job.id}">Cancelar</button>` : ''}
    </td>
  </tr>`).join('');

  $('#view').innerHTML = `
    ${active ? `<div class="panel section" style="margin-top:0">
      <div class="panel-head"><h2>Tarea en curso</h2>${status(active.state === 'running' ? 'Ejecutando' : 'En cola', active.state === 'running' ? 'info' : 'warn')}</div>
      <div class="panel-body" style="padding-top:4px;padding-bottom:6px">
        <div class="job-active" style="border-bottom:0">
          ${active.state === 'running' ? '<span class="spinner" aria-hidden="true"></span>' : icon('clock')}
          <div class="info"><strong>${esc(jobLabel(active.kind))} <span class="faint" style="font-weight:400">#${active.id}</span></strong><small id="jobActiveMessage">${esc(active.message || 'Preparando…')}</small></div>
          <span id="jobActiveBar">${bar(active.progress || 0)}</span>
          <span class="badge mono" id="jobActivePct">${Math.round((active.progress || 0) * 100)}%</span>
          <button class="btn btn-sm btn-danger" data-action="job-cancel" data-id="${active.id}">Cancelar</button>
        </div>
      </div>
    </div>` : ''}

    <div class="section" style="margin-top:${active ? '20px' : '0'}">
      <div class="section-head"><h2>Tareas rápidas</h2><span>acciones habituales de sincronización</span></div>
      <div class="task-grid">${quickTasks.map(taskCard).join('')}</div>
    </div>

    <details class="adv-box section">
      <summary>${icon('wrench', 'sm')} Tareas avanzadas y de mantenimiento <span class="tiny faint" style="margin-left:auto">${advancedTasks.length} tareas</span></summary>
      <div class="adv-box-body">
        <div class="task-grid">${advancedTasks.map(taskCard).join('')}</div>
      </div>
    </details>

    <div class="panel flush section">
      <div class="panel-head"><h2>Ejecuciones recientes</h2><span class="right tiny faint">${jobs.length} trabajos · <a href="#/events">ver eventos</a></span></div>
      ${rows ? `<div class="table-wrap"><table class="table--stack">
        <thead><tr><th>Estado</th><th>Tarea</th><th>Mensaje</th><th>Duración</th><th>Cuándo</th><th></th></tr></thead>
        <tbody>${rows}</tbody>
      </table></div>` : empty('Sin tareas', 'Lanza una tarea desde las tarjetas de arriba.')}
    </div>
  `;
}
