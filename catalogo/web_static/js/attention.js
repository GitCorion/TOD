import { esc, fmtRelative } from './format.js';
import { icon, status } from './ui.js';

const TONES = {
  fail: { label: 'Fallo', tone: 'bad', icon: 'alert' },
  warn: { label: 'Aviso', tone: 'warn', icon: 'alert' },
  info: { label: 'Info', tone: 'info', icon: 'clock' },
};

export function severityTone(severity) {
  return (TONES[severity] || TONES.info).tone;
}

export function attentionCount(data) {
  const counts = data?.counts || {};
  return Number(counts.fail || 0) + Number(counts.warn || 0);
}

export function attentionItem(item) {
  const meta = TONES[item.severity] || TONES.info;
  const since = item.since ? `<span class="tiny faint">desde ${esc(fmtRelative(item.since))}</span>` : '';
  return `<div class="attention-item ${meta.tone}">
    <span class="attention-mark">${icon(meta.icon)}</span>
    <div class="attention-copy">
      <strong>${esc(item.title)}</strong>
      ${item.detail ? `<p>${esc(item.detail)}</p>` : ''}
      ${item.hint ? `<span class="tiny faint">${esc(item.hint)}</span>` : ''}
    </div>
    <div class="attention-side">
      ${status(meta.label, meta.tone)}
      ${since}
      ${item.href ? `<a class="btn btn-sm btn-ghost" href="${esc(item.href)}">${esc(item.action || 'Abrir')}</a>` : ''}
    </div>
  </div>`;
}

export function attentionCard(data, { compact = true, id = 'attentionPanel' } = {}) {
  if (!data) {
    return `<div class="attention-card unavailable" id="${id}">
      <span class="attention-mark">${icon('alert')}</span>
      <div class="attention-copy"><strong>No se pudo comprobar el estado</strong><span class="tiny faint">Reintenta en unos segundos. Si acabas de actualizar TOD, reinicia el servicio.</span></div>
      <button class="btn btn-sm btn-ghost" data-action="attention-check">${icon('refresh', 'sm')} Reintentar</button>
    </div>`;
  }
  const counts = data?.counts || {};
  const items = data?.items || [];
  if (!items.length) {
    return `<div class="attention-card ok" id="${id}">
      <span class="attention-mark">${icon('check')}</span>
      <div class="attention-copy"><strong>Todo en orden</strong><span class="tiny faint">Sin avisos pendientes${data?.generated_at ? ` · comprobado ${fmtRelative(data.generated_at)}` : ''}.</span></div>
      <button class="btn btn-sm btn-ghost" data-action="attention-check">${icon('refresh', 'sm')} Comprobar ahora</button>
    </div>`;
  }
  const fails = Number(counts.fail || 0);
  const shown = compact ? items.slice(0, 4) : items;
  const title = fails ? 'Necesita atención' : 'Para revisar';
  return `<section class="panel attention-panel ${fails ? 'fail' : 'warn'}" id="${id}" aria-label="${title}">
    <div class="panel-head attention-head">
      <h2>${title}</h2>
      <span class="right tiny faint">${counts.total} aviso${counts.total === 1 ? '' : 's'}${data.generated_at ? ` · ${fmtRelative(data.generated_at)}` : ''}</span>
      <button class="btn btn-sm btn-ghost" data-action="attention-check">${icon('refresh', 'sm')} Comprobar</button>
    </div>
    <div class="attention-list">${shown.map(attentionItem).join('')}</div>
    ${compact && items.length > shown.length
      ? `<div class="attention-more"><a class="tiny" href="#/system">Ver los ${items.length} avisos</a></div>`
      : ''}
  </section>`;
}

export function attentionSummary(data) {
  if (!data) return status('Sin datos de estado', 'muted');
  const counts = data?.counts || {};
  if (!counts.total) return status('Todo en orden', 'ok');
  if (counts.fail) return status(`${counts.fail} fallo${counts.fail === 1 ? '' : 's'}`, 'bad');
  return status(`${counts.warn} aviso${counts.warn === 1 ? '' : 's'}`, 'warn');
}
