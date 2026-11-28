import { api, apiPost } from '../api.js';
import { esc, fmtInt, fmtRelative } from '../format.js';
import { originLabel } from '../labels.js';
import { state } from '../store.js';
import { $, badge, empty, icon, poster, setHeader, status, thumb, toast } from '../ui.js';

const MAX_TORRENT_BYTES = 600000;

function countLabel(value, one, many) {
  const count = Number(value) || 0;
  return `${fmtInt(count)} ${count === 1 ? one : many}`;
}

function typeLabel(row) {
  if (row.kind === 'movie') return 'Película';
  if (row.kind === 'torrent') return 'Torrent';
  if (row.kind !== 'series') return '—';
  if (row.season && row.episode) {
    return `Serie, T${String(row.season).padStart(2, '0')}E${String(row.episode).padStart(2, '0')}`;
  }
  if (row.season) return `Serie, T${String(row.season).padStart(2, '0')}`;
  return 'Serie';
}

function rowState(row) {
  switch (row.state) {
    case 'added': return status('Añadido', 'ok');
    case 'ready': return status('Listo', 'ok');
    case 'duplicate': return status('Ya está', 'info');
    case 'needs_range': return status('Elige temporada', 'warn');
    case 'error': return status('Error', 'bad');
    default: return status('No encontrado', 'muted');
  }
}

function batchSummary(batch) {
  const parts = [];
  if (batch.added) parts.push(countLabel(batch.added, 'añadido', 'añadidos'));
  if (batch.duplicate) parts.push(countLabel(batch.duplicate, 'ya estaba', 'ya estaban'));
  if (batch.needs_range) parts.push(countLabel(batch.needs_range, 'sin temporada', 'sin temporada'));
  if (batch.unknown) parts.push(countLabel(batch.unknown, 'sin identificar', 'sin identificar'));
  if (batch.errors) parts.push(countLabel(batch.errors, 'con error', 'con error'));
  return parts.join(', ') || 'Sin líneas';
}

function batchState(batch) {
  if (batch.state === 'running') return status('En curso', 'info');
  if (batch.state === 'error') return status('Con error', 'bad');
  if (batch.state === 'partial') return status('Parcial', 'warn');
  return status('Completado', 'ok');
}

function lines(text) {
  return String(text || '')
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line && !line.startsWith('#'));
}

function isStructuredLine(line) {
  return /themoviedb\.org/i.test(line)
    || /^(movie|series):/i.test(line)
    || /^(tmdb|id),/i.test(line)
    || /^\d{1,9}$/.test(line);
}

function looksLikeList(text) {
  const items = lines(text);
  if (items.length > 1) return true;
  return items.some((line) => /themoviedb\.org/i.test(line)
    || /^(movie|series):/i.test(line)
    || /^(tmdb|id),/i.test(line));
}

function resultTable(items) {
  const rows = (items || []).map((item) => {
    const title = item.title || item.raw || '—';
    const href = item.kind === 'torrent'
      ? '#/downloads'
      : item.media_id
        ? `#/title/${item.media_id}`
        : item.kind === 'series' && item.tmdb
          ? `#/series/${item.tmdb}`
          : '';
    return `<tr>
      <td data-label="Título">
        <div class="episode-title-cell">
          ${thumb(item.poster_path, title)}
          <div class="cell-title">
            <strong>${esc(title)}</strong>
            <small>${esc(item.title ? (item.raw || '') : '')}</small>
          </div>
        </div>
      </td>
      <td data-label="Tipo">${esc(typeLabel(item))}</td>
      <td data-label="Estado">${rowState(item)}</td>
      <td data-label="Detalle"><span class="tiny muted">${esc(item.detail || '—')}</span></td>
      <td class="actions" data-label="">
        ${href ? `<a class="btn btn-sm btn-ghost" href="${href}">Abrir</a>` : ''}
      </td>
    </tr>`;
  }).join('');
  return rows
    ? `<div class="table-wrap"><table class="table--stack">
        <thead><tr><th>Título</th><th>Tipo</th><th>Estado</th><th>Detalle</th><th></th></tr></thead>
        <tbody>${rows}</tbody>
      </table></div>`
    : '';
}

function renderBatch(result) {
  const target = $('#addResult');
  if (!target) return;
  const added = Number(result.added || 0);
  const title = added ? 'Añadido' : 'Sin cambios';
  const items = result.items || [];
  const shown = items.slice(0, 12);
  const rest = items.length - shown.length;
  target.innerHTML = `
    <div class="section-head">
      <h2>${esc(title)}</h2>
      <span>${esc(batchSummary(result))}</span>
      ${result.id ? `<a class="right tiny" href="#/add/${result.id}">Ver lote</a>` : ''}
    </div>
    ${resultTable(shown)}
    ${rest ? `<p class="tiny faint" style="margin:10px 0 0">Mostrando ${fmtInt(shown.length)} de ${fmtInt(items.length)}. <a href="#/add/${result.id}">Ver el lote completo</a>.</p>` : ''}
  `;
}

function renderSearch(items) {
  const target = $('#addResult');
  if (!target) return;
  state.addResults = items;
  if (!items.length) {
    target.innerHTML = `<div class="panel pad">${empty(
      'Sin resultados',
      'Prueba otro título o pega el enlace de TMDB.',
    )}</div>`;
    return;
  }
  const rows = items.map((item, index) => {
    const kind = item.kind === 'series' ? 'Serie' : 'Película';
    const meta = `${item.year || '—'} · ${kind}${item.already ? ' · Ya en TOD' : ''}`;
    const posterHtml = `<span class="add-result-poster" style="width:46px">${poster(item.poster_path, item.title, { size: 'w154' })}</span>`;
    const main = `<span class="add-result-main"><strong>${esc(item.title || '—')}</strong><small>${esc(meta)}</small></span>`;
    if (item.already) {
      const href = item.kind === 'series' ? `#/series/${item.tmdb}` : `#/title/${item.asset_id}`;
      return `<div class="add-result" style="grid-template-columns:46px minmax(0,1fr) auto">${posterHtml}${main}<a class="btn btn-sm btn-ghost" href="${href}">Abrir</a></div>`;
    }
    return `<div class="add-result" style="grid-template-columns:46px minmax(0,1fr) auto">
      ${posterHtml}${main}
      <button class="btn btn-sm btn-primary" type="button" data-action="add-result" data-index="${index}">
        <span class="add-result-action" style="color:inherit">${icon('plus', 'sm')} Añadir</span>
      </button>
    </div>`;
  }).join('');
  target.innerHTML = `
    <div class="section-head"><h2>Resultados</h2><span>Elige qué añadir</span></div>
    <div class="add-results">${rows}</div>
  `;
}

function renderError(error) {
  const target = $('#addResult');
  if (!target) return;
  const message = error?.message || String(error);
  const tmdb = /TMDB/i.test(message);
  target.innerHTML = `<div class="panel pad">${empty(
    'No se pudo añadir',
    message,
    tmdb ? '<a class="btn btn-sm" href="#/settings?section=conexiones&focus=tmdb_api_key">Abrir configuración</a>' : '',
  )}</div>`;
}

function recentInner(data) {
  const batches = (data.batches || []).slice(0, 4);
  if (!batches.length) {
    return `<div class="empty">Todavía no has añadido nada.</div>`;
  }
  return `<div class="add-recent">${batches.map((batch) => `
    <a class="add-recent-row" href="#/add/${batch.id}">
      <span class="badge mono">${esc(originLabel(batch.source))}</span>
      <span class="add-recent-main">
        <strong>${esc(batch.label || 'Añadido')}</strong>
        <small>${esc(fmtRelative(batch.created_at))}, ${esc(batchSummary(batch))}</small>
      </span>
      ${batchState(batch)}
    </a>`).join('')}</div>`;
}

async function refreshRecent() {
  try {
    const data = await api('/api/intake?flows=0');
    const host = $('#addRecent');
    if (host) {
      host.innerHTML = recentInner(data);
      return;
    }
    if ((data.batches || []).length) {
      $('#view').insertAdjacentHTML(
        'beforeend',
        `<details class="fold section" id="addRecent"><summary>Últimas entradas</summary>${recentInner(data)}</details>`,
      );
    }
  } catch (_) {
    /* la recepción es secundaria */
  }
}

function syncSubmitLabel() {
  const button = $('#addSubmit');
  if (!button || button.disabled) return;
  const text = String($('#addText')?.value || '').trim();
  button.textContent = !text || !looksLikeList(text) ? 'Buscar' : 'Añadir';
}

async function searchTitles(text) {
  const button = $('#addSubmit');
  const previous = button?.textContent || 'Añadir';
  if (button) {
    button.disabled = true;
    button.textContent = 'Buscando…';
  }
  try {
    const query = encodeURIComponent(text);
    const [movies, series] = await Promise.all([
      api(`/api/tmdb/search?kind=movie&q=${query}`).catch(() => ({ results: [] })),
      api(`/api/tmdb/search?kind=series&q=${query}`).catch(() => ({ results: [] })),
    ]);
    const items = [...(movies.results || []), ...(series.results || [])]
      .filter((item) => item && item.tmdb)
      .sort((a, b) => (Number(b.popularity) || 0) - (Number(a.popularity) || 0))
      .slice(0, 8);
    renderSearch(items);
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = previous;
      syncSubmitLabel();
    }
  }
}

async function importList(text) {
  const button = $('#addSubmit');
  const previous = button?.textContent || 'Añadir';
  const follow = Boolean($('#addFollow')?.checked);
  if (button) {
    button.disabled = true;
    button.textContent = 'Añadiendo…';
  }
  try {
    const result = await apiPost('/api/intake/import', {
      text,
      kind: 'movie',
      follow,
    });
    renderBatch(result);
    const added = Number(result.added || 0);
    const duplicate = Number(result.duplicate || 0);
    toast(
      added
        ? `${countLabel(added, 'título añadido', 'títulos añadidos')}${duplicate ? `, ${countLabel(duplicate, 'ya estaba', 'ya estaban')}` : ''}`
        : 'No se añadió ningún título',
      added ? 'ok' : 'warn',
    );
    await refreshRecent();
  } catch (error) {
    renderError(error);
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = previous;
      syncSubmitLabel();
    }
  }
}

export async function submitAdd(form) {
  const text = String(form.elements.text?.value || '').trim();
  if (!text) {
    form.elements.text?.focus();
    toast('Escribe un título o pega un enlace', 'warn');
    return;
  }
  const items = lines(text);
  if (items.length > 1 && !items.some(isStructuredLine)) {
    renderError(new Error(
      'Para añadir varios títulos, pega enlaces o IDs de TMDB, uno por línea. Para uno solo, escribe su nombre.',
    ));
    return;
  }
  if (looksLikeList(text)) await importList(text);
  else await searchTitles(text);
}

export async function addSearchResult(element) {
  const item = state.addResults?.[Number(element.dataset.index)];
  if (!item) return;
  const follow = Boolean($('#addFollow')?.checked);
  const action = element.querySelector('.add-result-action');
  const previous = action?.innerHTML || '';
  element.disabled = true;
  if (action) action.textContent = 'Añadiendo…';
  try {
    if (item.kind === 'series') {
      const detail = await api(`/api/tmdb/seasons/${item.tmdb}`);
      const seasons = (detail.seasons || [])
        .filter((season) => Number(season.season) > 0)
        .map((season) => Number(season.season));
      if (!seasons.length) throw new Error('La serie no tiene temporadas');
      const result = await apiPost('/api/titles', {
        kind: 'series',
        tmdb: item.tmdb,
        seasons,
        origin: 'import',
        follow,
      });
      toast(
        `${item.title}: ${fmtInt(result.episodes || 0)} episodios añadidos${follow ? '' : ' sin seguimiento'}`,
        'ok',
      );
    } else {
      await apiPost('/api/titles', {
        kind: 'movie',
        tmdb: item.tmdb,
        origin: 'import',
        follow,
      });
      toast(`${item.title} añadido${follow ? '' : ' sin seguimiento'}`, 'ok');
    }
    if (action) action.innerHTML = `${icon('check', 'sm')} Añadido`;
    element.disabled = true;
  } catch (error) {
    element.disabled = false;
    if (action) action.innerHTML = previous;
    renderError(error);
  }
}

function readBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result || '');
      resolve(result.includes(',') ? result.split(',', 2)[1] : result);
    };
    reader.onerror = () => reject(new Error('No se pudo leer el archivo'));
    reader.readAsDataURL(file);
  });
}

export async function uploadTorrent(file) {
  if (!file) return;
  if (file.size > MAX_TORRENT_BYTES) {
    toast('El .torrent es demasiado grande', 'error');
    return;
  }
  const button = $('[data-action="torrent-pick"]');
  const previous = button?.innerHTML || 'Subir .torrent';
  if (button) {
    button.disabled = true;
    button.textContent = 'Subiendo…';
  }
  try {
    const data = await readBase64(file);
    const result = await apiPost('/api/intake/torrent', {
      filename: file.name,
      data,
    });
    const target = $('#addResult');
    if (target) {
      target.innerHTML = `<div class="add-torrent-result">
        <div>
          <strong>${esc(result.name || file.name)}</strong>
          <small>${esc((result.info_hash || '').slice(0, 12))}, ${esc(result.save_path || '')}</small>
        </div>
        <a class="btn btn-sm" href="#/downloads">Ver descargas</a>
      </div>`;
    }
    toast(`Torrent añadido: ${result.name || file.name}`, 'ok');
    await refreshRecent();
  } catch (error) {
    toast(error.message, 'error');
  } finally {
    if (button) {
      button.disabled = false;
      button.innerHTML = previous;
    }
    const input = $('#torrentFile');
    if (input) input.value = '';
  }
}

function batchDetailView(data) {
  const items = data.items || [];
  return `
    <div class="row between" style="margin-bottom:14px">
      <a class="tiny" href="#/add">${icon('arrow-left', 'sm')} Añadir</a>
      <span class="tiny faint">${esc(fmtRelative(data.created_at))}</span>
    </div>
    <div class="panel section">
      <div class="panel-head">
        <h2>${esc(data.label || 'Añadido')}</h2>
        ${batchState(data)}
      </div>
      <div class="panel-body">
        <div class="add-batch-summary">${esc(batchSummary(data))}</div>
      </div>
    </div>
    <div class="panel flush section">
      ${resultTable(items) || empty('Lote vacío', 'No se registró ninguna línea.')}
    </div>
  `;
}

export async function renderAdd({ query, params, signal }) {
  const batchId = Number(params[0] || 0);
  if (batchId) {
    const data = await api(`/api/intake/batches/${batchId}`, { signal });
    setHeader(`Lote #${batchId}`);
    $('#view').innerHTML = batchDetailView(data);
    return;
  }

  setHeader('Añadir');
  const recent = await api('/api/intake?flows=0', { signal }).catch(() => ({ batches: [] }));
  const initial = query.get('q') || '';
  $('#view').innerHTML = `
    <form class="panel pad add-search" data-form="add-submit">
      <div class="add-search-main">
        <label class="add-search-field">
          ${icon('search')}
          <textarea id="addText" name="text" rows="2" spellcheck="false"
            placeholder="Busca un título, o pega un enlace de TMDB o una lista"
            aria-describedby="addHelp">${esc(initial)}</textarea>
        </label>
        <button type="submit" class="btn btn-primary" id="addSubmit">Buscar</button>
      </div>
      <div class="add-search-options">
        <label class="inline check" title="Si ahora no hay fuente, TOD buscará releases automáticamente cuando aparezcan">
          <input type="checkbox" id="addFollow" checked> Monitorizar automáticamente si falta fuente
        </label>
        <div class="row">
          <button type="button" class="btn btn-sm btn-ghost" data-action="torrent-pick">
            ${icon('upload', 'sm')} Subir .torrent
          </button>
        </div>
      </div>
      <input type="file" id="torrentFile" accept=".torrent,application/x-bittorrent" class="hidden">
      <details class="adv-box" style="margin-top:10px">
        <summary style="padding:6px 10px;font-size:12px;font-weight:500">Importación por lotes, enlaces TMDB o .torrent</summary>
        <div class="adv-box-body" style="padding:8px 10px">
          <p class="tiny faint add-help" id="addHelp">
            Pulsa <kbd>Enter</kbd> para buscar por título, o <kbd>Mayús+Enter</kbd> para pegar varias líneas (enlaces o IDs de TMDB) y añadirlas de golpe. También puedes subir un fichero <span class="mono">.torrent</span> directo a qBittorrent.
          </p>
        </div>
      </details>
    </form>

    <div class="add-layout">
      <div class="add-main">
        <div id="addResult" class="add-result-host" aria-live="polite">
          <div class="panel pad">${empty('Busca un título', 'Escribe el nombre, o pega un enlace de TMDB. Los resultados aparecen aquí.')}</div>
        </div>
      </div>
      <aside class="add-aside" aria-label="Últimas entradas">
        <div class="panel">
          <div class="panel-head"><h2>Últimas entradas</h2></div>
          <div class="panel-body flush" id="addRecent">${recentInner(recent)}</div>
        </div>
      </aside>
    </div>
  `;
  const textarea = $('#addText');
  textarea?.addEventListener('input', syncSubmitLabel);
  textarea?.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      textarea.form?.requestSubmit();
    }
  });
  syncSubmitLabel();
  if (initial) {
    if (looksLikeList(initial)) await importList(initial);
    else await searchTitles(initial);
  }
}
