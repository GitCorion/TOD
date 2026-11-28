import { api } from '../api.js';
import { esc, fmtDate, fmtRelative } from '../format.js';
import { eventLabel } from '../labels.js';
import { navigate } from '../router.js';
import { $, badge, empty, icon, setHeader } from '../ui.js';

let logsTimer = null;

function clearLogsTimer() {
  if (logsTimer) {
    clearInterval(logsTimer);
    logsTimer = null;
  }
}

function renderEventsTab(events) {
  const items = events.items || [];
  if (!items.length) {
    return `<div class="panel pad">${empty('Sin eventos recientes', 'Las acciones y cambios de estado aparecerán aquí.')}</div>`;
  }
  const rows = items.map((event) => `<tr>
    <td data-label="Cuándo" class="nowrap">
      <span title="${esc(fmtDate(event.ts))}">${esc(fmtRelative(event.ts))}</span>
    </td>
    <td data-label="Evento">${badge(eventLabel(event.kind))}</td>
    <td data-label="Título">${event.asset_id ? `<a href="#/title/${event.asset_id}">Abrir ficha</a>` : '<span class="faint">—</span>'}</td>
    <td data-label="Detalle">
      <details class="event-payload">
        <summary>ver datos</summary>
        <pre>${esc(event.payload || '')}</pre>
      </details>
    </td>
  </tr>`).join('');
  return `<div class="panel flush">
    <div class="panel-head">
      <h2>Actividad y eventos</h2>
      <span class="right tiny faint">últimos ${items.length} eventos</span>
    </div>
    <div class="table-wrap"><table class="table--stack">
      <thead><tr><th>Cuándo</th><th>Evento</th><th>Título</th><th>Detalle</th></tr></thead>
      <tbody>${rows}</tbody>
    </table></div>
  </div>`;
}

function renderLogRows(items) {
  if (!items.length) {
    return `<div class="pad">${empty('Sin registros', 'No hay líneas de registro que coincidan con el filtro.')}</div>`;
  }
  return items.map((entry) => `<div class="log-row">
    <span class="log-ts">${esc(entry.ts || '')}</span>
    <span class="log-lvl ${esc(entry.level || 'INFO')}">${esc(entry.level || 'INFO')}</span>
    <span class="log-logger" title="${esc(entry.logger || '')}">${esc(entry.logger || '')}</span>
    <span class="log-msg">${esc(entry.message || '')}</span>
  </div>`).join('');
}

export async function renderEvents({ query, signal, tab = 'events' }) {
  clearLogsTimer();
  const activeTab = tab === 'logs' ? 'logs' : 'events';
  setHeader(activeTab === 'logs' ? 'Registros' : 'Eventos');

  if (activeTab === 'events') {
    const events = await api('/api/events?limit=100', { signal });
    $('#view').innerHTML = renderEventsTab(events);
    return;
  }

  const level = query.get('level') || '';
  const q = query.get('q') || '';
  const params = new URLSearchParams({ limit: '250', level, q });
  const data = await api(`/api/system/logs?${params}`, { signal });
  const items = data.items || [];

  $('#view').innerHTML = `
    <div class="panel flush">
      <div class="log-toolbar">
        <div class="seg seg-quiet" role="group" aria-label="Nivel de registro">
          ${[
            ['', 'Todos'],
            ['INFO', 'Info'],
            ['WARNING', 'Avisos'],
            ['ERROR', 'Errores'],
          ].map(([val, label]) => `<button class="seg-item ${level === val ? 'active' : ''}" data-action="logs-level" data-level="${val}">${label}</button>`).join('')}
        </div>
        <label class="library-search" style="flex:1 1 220px;max-width:340px;height:30px">
          ${icon('search')}
          <input id="logsQuery" value="${esc(q)}" placeholder="Filtrar registros…" autocomplete="off" aria-label="Filtrar registros">
        </label>
        <div class="row" style="margin-left:auto">
          <label class="inline check tiny">
            <input type="checkbox" id="logsAuto" checked> Auto-refresco (5s)
          </label>
          <button class="btn btn-sm btn-ghost" data-action="logs-refresh">${icon('refresh', 'sm')} Actualizar</button>
        </div>
      </div>
      <div class="log-viewer" id="logViewer">
        ${renderLogRows(items)}
      </div>
    </div>
  `;

  const reloadLogs = async () => {
    const viewer = $('#logViewer');
    if (!viewer) {
      clearLogsTimer();
      return;
    }
    try {
      const currentQ = $('#logsQuery')?.value || '';
      const qs = new URLSearchParams({ limit: '250', level, q: currentQ });
      const next = await api(`/api/system/logs?${qs}`);
      viewer.innerHTML = renderLogRows(next.items || []);
    } catch (_) {
      /* ignorar errores transitorios de polling */
    }
  };

  $('#logsQuery')?.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      navigate('logs', [], { level, q: event.target.value.trim() }, { replace: true });
    }
  });

  const autoCheck = $('#logsAuto');
  const syncTimer = () => {
    clearLogsTimer();
    if (autoCheck?.checked) {
      logsTimer = setInterval(() => {
        if (!document.hidden) reloadLogs();
      }, 5000);
    }
  };
  autoCheck?.addEventListener('change', syncTimer);
  syncTimer();
}
