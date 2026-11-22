import { api } from '../api.js';
import { esc, fmtBytes, fmtDate, fmtInt, fmtSpeed } from '../format.js';
import { state } from '../store.js';
import { $, badge, bar, empty, icon, setHeader, status } from '../ui.js';

const QBIT_STATES = [
  ['checking', 'Comprobando', 'warn'],
  ['queued', 'En cola', 'muted'],
  ['downloading', 'Descargando', 'info'],
  ['stalleddl', 'Descargando, sin pares', 'info'],
  ['forceddl', 'Descargando', 'info'],
  ['metadl', 'Descargando', 'info'],
  ['stalledup', 'Sembrando, sin pares', 'ok'],
  ['forcedup', 'Sembrando', 'ok'],
  ['uploading', 'Sembrando', 'ok'],
  ['paused', 'Pausado', 'muted'],
  ['stopped', 'Pausado', 'muted'],
  ['moving', 'Moviendo', 'warn'],
  ['error', 'Error', 'bad'],
  ['missing', 'Error', 'bad'],
];

const REASONS = {
  no_esta_sembrando: 'No está sembrando',
  tiempo_minimo_de_seed: 'Falta tiempo de seed',
  ratio_insuficiente: 'Falta ratio',
  sin_politica: 'Sin política',
  politica_cumplida: 'Listo',
};

function liveState(item) {
  if (!item.live) return { label: 'Sin datos', tone: 'muted' };
  const raw = String(item.live_state || item.torrent_state || '').toLowerCase();
  for (const [needle, label, tone] of QBIT_STATES) {
    if (raw.includes(needle)) return { label, tone };
  }
  if (item.state === 'seeding') return { label: 'Sin datos de qBittorrent', tone: 'muted' };
  if (item.state === 'active') return { label: 'Activo', tone: 'muted' };
  if (item.state === 'cancelled') return { label: 'Cancelado', tone: 'muted' };
  return { label: item.state || '—', tone: 'muted' };
}

function isSeeding(item) {
  const state = liveState(item);
  return state.label === 'Sembrando' || state.label === 'Sembrando, sin pares';
}

function isChecking(item) {
  const state = liveState(item);
  return state.label === 'Comprobando' || state.label === 'En cola';
}

export async function renderSeeds({ signal }) {
  const data = await api('/api/seeds', { signal });
  const items = data.items || [];
  state.seeds = items;
  setHeader('Siembra', 'Operación');

  const rows = items.map((item) => {
    const ratio = Number(item.live_ratio ?? 0);
    const target = Number(item.target_ratio || 0);
    const ratioPct = target > 0 ? Math.min(1, ratio / target) : null;
    const live = liveState(item);
    const reason = REASONS[item.finish_reason] || item.finish_reason || '—';
    const canFinish = item.can_finish && isSeeding(item);
    const finishText = !item.live
      ? 'Sin datos'
      : live.label === 'Comprobando' || live.label === 'En cola'
        ? live.label
        : item.can_finish && isSeeding(item) ? 'Listo' : reason;
    return `<tr>
      <td data-label="Título"><div class="cell-title"><strong>${esc(item.title || '—')}</strong><small>${esc(item.key || '')}</small></div></td>
      <td data-label="Tracker">${esc(item.tracker || '—')}</td>
      <td data-label="Estado">${status(live.label, live.tone)}</td>
      <td data-label="Ratio">
        ${ratioPct !== null
          ? `<div class="progress-cell">${bar(ratioPct, ratioPct >= 1 ? 'green' : '')}<span>${ratio.toFixed(2)}/${target}</span></div>`
          : `<span>${ratio.toFixed(2)}</span>`}
        ${item.live_upspeed ? `<div class="tiny faint">↑ ${fmtSpeed(item.live_upspeed)}</div>` : ''}
      </td>
      <td data-label="Tamaño" class="num">${fmtBytes(item.size_bytes)}</td>
      <td data-label="Requerido hasta">${fmtDate(item.seed_required_until)}</td>
      <td data-label="Mínimo cumplido">${canFinish ? status('Listo', 'ok') : `<span class="tiny faint">${esc(finishText)}</span>`}</td>
      <td class="actions" data-label="">
        <button class="btn btn-sm" data-action="seed-finish" data-id="${item.id}" ${canFinish ? '' : 'disabled'}>Soltar</button>
        ${item.media_id ? `<a class="btn btn-sm btn-ghost" href="#/title/${item.media_id}">Ficha</a>` : ''}
      </td>
    </tr>`;
  }).join('');

  $('#view').innerHTML = `
    <div class="stats">
      <div class="stat"><div class="label">Compromisos</div><div class="value">${fmtInt(items.length)}</div><div class="hint">${fmtInt(items.filter((item) => !item.live).length)} sin datos de qBittorrent</div></div>
      <div class="stat green"><div class="label">Sembrando</div><div class="value">${fmtInt(items.filter(isSeeding).length)}</div><div class="hint">con qBittorrent activo</div></div>
      <div class="stat amber"><div class="label">Comprobando o en cola</div><div class="value">${fmtInt(items.filter(isChecking).length)}</div><div class="hint">estado de qBittorrent</div></div>
      <div class="stat accent"><div class="label">Listos para soltar</div><div class="value">${fmtInt(items.filter((item) => item.can_finish && isSeeding(item)).length)}</div><div class="hint">qBittorrent decide el borrado</div></div>
    </div>

    <div class="panel section">
      <div class="panel-head"><h2>Mantenimiento</h2><span class="right tiny faint">TOD no borra torrents ni ficheros</span></div>
      <div class="panel-body">
        <div class="chips">
          <button class="chip" data-action="open-task" data-kind="seeding">${icon('refresh', 'sm')} Mantener siembra mínima</button>
          <a class="chip" href="#/settings?section=siembra">Ajustes de siembra</a>
        </div>
        <p class="muted tiny" style="margin:12px 0 0">TOD fuerza el seed hasta cumplir horas y ratio. Después lo suelta y qBittorrent aplica sus propias reglas; TOD no elimina el torrent ni su contenido.</p>
      </div>
    </div>

    <div class="panel flush section">
      <div class="panel-head"><h2>Compromisos de siembra</h2><span class="right tiny faint">${items.length} registros</span></div>
      ${rows ? `<div class="table-wrap"><table class="table--stack">
        <thead><tr><th>Título</th><th>Tracker</th><th>Estado</th><th>Ratio</th><th>Tamaño</th><th>Requerido hasta</th><th>Mínimo cumplido</th><th></th></tr></thead>
        <tbody>${rows}</tbody>
      </table></div>` : empty('Sin compromisos', 'Las descargas nuevas crearán uno al prepararse.')}
    </div>
  `;
}
