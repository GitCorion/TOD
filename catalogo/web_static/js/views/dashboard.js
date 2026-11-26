import { api } from '../api.js';
import { attentionCard, paintAttention } from '../attention.js';
import { esc, fmtBytes, fmtInt, fmtRelative } from '../format.js';
import { jobLabel, jobState } from '../labels.js';
import { state } from '../store.js';
import { $, badge, barPercent, empty, icon, poster, setHeader } from '../ui.js';

function withTimeout(promise, ms, fallback) {
  let timer = null;
  const timeout = new Promise((resolve) => {
    timer = setTimeout(() => resolve(fallback), ms);
  });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}

export async function renderDashboard({ signal }) {
  // La portada no puede esperar 15 s a qBittorrent para pintar el catálogo:
  // lo local se muestra ya y lo que no llegue se marca como sin respuesta.
  const [data, attention, activity, jobsData, preview] = await Promise.all([
    withTimeout(
      api('/api/ondemand/overview', { signal }),
      6000,
      state.overview || null,
    ),
    api('/api/attention', { signal }).catch(() => null),
    state.activity
      ? Promise.resolve(state.activity)
      : api('/api/activity', { signal }).catch(() => null),
    api('/api/jobs?limit=30', { signal }).catch(() => ({ items: [] })),
    api('/api/ondemand/recent?limit=6', { signal }).catch(() => ({ items: [] })),
  ]);
  state.attention = attention;
  paintAttention(attention);
  const hasData = Boolean(data && data.generated_at);
  if (hasData) state.overview = data;
  const routine = new Set(['attention-check', 'qbit-limits', 'worker-cycle']);
  const recentJobs = (jobsData.items || []).filter((job) => !routine.has(job.kind));
  const catalog = data?.catalog || {};
  const movies = catalog.movies || {};
  const series = catalog.series || {};
  const downloads = data?.downloads || {};
  const library = data?.library || {};
  const disk = data?.disk || {};
  const active = activity?.running || (activity?.active || [])[0] || null;
  const latest = recentJobs[0] || null;
  const availableCount = (movies.published || 0) + (series.published || 0);
  const usedPct = disk.total ? Math.round(((disk.total - (disk.free || 0)) / disk.total) * 100) : 0;
  const pendingCount = (movies.pending || 0) + (series.pending || 0);
  const num = (value) => (hasData ? fmtInt(value) : '—');
  const bytes = (value) => (hasData ? fmtBytes(value) : '—');

  setHeader('Inicio');

  const recent = (library.recent || []).slice(0, 7).map((item) => {
    const meta = item.kind === 'movie'
      ? `Película, ${item.year || '—'}`
      : `T${String(item.season || 0).padStart(2, '0')}E${String(item.episode || 0).padStart(2, '0')}`;
    const href = item.kind === 'movie' && item.asset_id
      ? `#/title/${item.asset_id}`
      : item.kind === 'series' && item.series_tmdb
        ? `#/series/${item.series_tmdb}`
        : `#/catalog?q=${encodeURIComponent(item.title || '')}`;
    return `<a class="media-row" href="${href}">
      <div class="info"><strong>${esc(item.title || '—')}</strong><small>${esc(meta)}</small></div>
      <span class="tiny faint nowrap">${esc(fmtRelative(item.date))}</span>
      ${icon('chevron', 'sm')}
    </a>`;
  }).join('');

  const activePanel = active
    ? `<div class="job-active">
        <span class="spinner" aria-hidden="true"></span>
        <div class="info">
          <strong>${esc(jobLabel(active.kind))}</strong>
          <small id="dashJobMessage">${esc(active.message || 'En curso')}, ${active.state === 'queued' ? 'en cola' : `${Math.round((active.progress || 0) * 100)} %`}</small>
        </div>
        ${barPercent((active.progress || 0) * 100)}
        <a class="btn btn-sm btn-ghost" href="#/jobs">Ver tarea</a>
      </div>`
    : latest
      ? `<div class="job-active">
          <span class="status ${jobState(latest.state).tone}" style="min-width:8px"></span>
          <div class="info">
            <strong>${esc(jobLabel(latest.kind))}</strong>
            <small>Última tarea, ${esc(jobState(latest.state).label)}, ${esc(fmtRelative(latest.finished_at || latest.created_at))}</small>
          </div>
        </div>`
      : `<div class="job-active"><div class="info"><strong>Sin tareas recientes</strong><small>Nada en marcha.</small></div><a class="btn btn-sm btn-ghost" href="#/jobs">Abrir tareas</a></div>`;

  $('#view').innerHTML = `
    <div class="section" style="margin-top:0">${attentionCard(attention, { compact: true })}</div>
    ${hasData ? '' : `<div class="notice section">No se pudo leer el resumen del servidor a tiempo. Los contadores se actualizarán al recargar.</div>`}

    <div class="kpi-grid section">
      <a class="kpi accent" href="#/catalog?publication=publicado" title="Películas, series y episodios que ya están disponibles.">
        <span class="label">Disponible</span>
        <span class="value">${num(availableCount)}</span>
        <span class="hint">${hasData ? `${fmtInt(movies.published || 0)} películas, ${fmtInt(series.published || 0)} series` : 'Sin datos del servidor'}</span>
        <span class="hint">${hasData ? `${fmtInt(catalog.episodes || 0)} episodios en total` : ''}</span>
      </a>
      <a class="kpi ${pendingCount ? 'amber' : ''}" href="#/catalog?publication=pendiente" title="Tienen fuente o fichero local; falta generar su acceso .strm.">
        <span class="label">Pendiente de .strm</span>
        <span class="value">${num(pendingCount)}</span>
        <span class="hint">${hasData ? 'con fuente, sin acceso .strm' : 'Sin datos del servidor'}</span>
      </a>
      <a class="kpi ${downloads.error ? 'red' : downloads.downloading ? 'amber' : ''}" href="#/downloads" title="Ver cola de descargas en qBittorrent.">
        <span class="label">Descargando</span>
        <span class="value">${num(downloads.downloading || 0)}</span>
        <span class="hint">${hasData ? `${fmtInt(downloads.seeding || 0)} sembrando, ${fmtInt(downloads.paused || 0)} pausadas` : 'Sin datos de qBittorrent'}</span>
      </a>
      <a class="kpi" href="#/system" title="Espacio libre en la carpeta de los .strm.">
        <span class="label">Espacio libre</span>
        <span class="value">${bytes(disk.free || 0)}</span>
        <span class="hint">${hasData ? `${usedPct} % usado de ${fmtBytes(disk.total || 0)}` : 'Sin datos del servidor'}</span>
      </a>
    </div>

    <div class="dash-grid section">
      <div class="panel">
        <div class="panel-head">
          <h2>Últimas incorporaciones</h2>
          <a class="right tiny faint" href="#/catalog">Ver biblioteca</a>
        </div>
        <div class="panel-body">
          ${(preview.items || []).length
            ? `<div class="poster-strip">
            ${(preview.items || []).map((item) => `<a class="strip-item" href="${item.kind === 'series' ? `#/series/${item.tmdb}` : `#/title/${item.id}`}">
              <span class="strip-poster">${poster(item.poster_path, item.title)}</span>
              <strong>${esc(item.title || '—')}</strong>
            </a>`).join('')}
          </div>`
            : empty('No hay títulos añadidos', 'Añade una película o serie para empezar.', `<a class="btn btn-sm" href="#/add">Añadir</a>`)}
        </div>
      </div>

      <div class="stack" style="gap:10px">
        <form class="panel" data-form="quick-search">
          <div class="panel-head"><h2>Añadir a TOD</h2></div>
          <div class="panel-body">
            <label class="field">
              <span>Título, enlace de TMDB o lista</span>
              <div class="quick-add-row">
                <input name="q" placeholder="El Señor de los Anillos…" autocomplete="off" aria-label="Añadir un título o una lista">
                <button class="btn btn-primary" type="submit">${icon('plus', 'sm')} Añadir</button>
              </div>
            </label>
            <p class="tiny faint" style="margin:6px 0 0">Acepta varias líneas y enlaces. Se abre la vista de alta para revisar el lote.</p>
          </div>
        </form>

        <div class="panel">
          <div class="panel-head"><h2>Cola de descargas</h2><a class="right tiny faint" href="#/downloads">Ver cola</a></div>
          <div class="panel-body" style="padding:2px 12px 4px">
            <div class="kv"><span>Descargando</span><span>${num(downloads.downloading || 0)}</span></div>
            <div class="kv"><span>Sembrando</span><span>${num(downloads.seeding || 0)}</span></div>
            <div class="kv"><span>Pausadas</span><span>${num(downloads.paused || 0)}</span></div>
            <div class="kv"><span>Con error</span><span class="${downloads.error ? 'error' : ''}">${num(downloads.error || 0)}</span></div>
          </div>
        </div>
      </div>
    </div>

    <div class="dash-grid section">
      <div class="panel">
        <div class="panel-head">
          <h2>Últimas tareas</h2>
          ${active ? badge('en curso', 'mono') : ''}
          <a class="right tiny faint" href="#/jobs">Ver tareas</a>
        </div>
        <div class="panel-body" style="padding:10px 12px">
          ${activePanel}
          ${recentJobs.length ? `<div style="margin-top:6px">
            ${recentJobs.filter((job) => job.id !== latest?.id && (!active || job.id !== active.id)).slice(0, 5).map((job) => `<div class="kv">
              <span><span class="status ${jobState(job.state).tone} no-dot">${esc(jobState(job.state).label)}</span> ${esc(jobLabel(job.kind))}</span>
              <span class="tiny faint">${esc(fmtRelative(job.finished_at || job.created_at))}</span>
            </div>`).join('')}
          </div>` : ''}
        </div>
      </div>

      <div class="panel">
        <div class="panel-head"><h2>Radarr y Sonarr</h2><span class="right tiny faint">solo lectura</span></div>
        <div class="panel-body" style="padding:2px 12px 4px">
          <div class="kv"><span>Películas con fichero</span><span>${num(library.radarr?.movies_available || 0)}</span></div>
          <div class="kv"><span>Series con fichero</span><span>${num(library.sonarr?.series_available || 0)}</span></div>
          <div class="kv"><span>Episodios</span><span>${hasData ? `${fmtInt(library.sonarr?.episodes_available || 0)} de ${fmtInt(library.sonarr?.episodes_total || 0)}` : '—'}</span></div>
          <div class="kv"><span>Cola</span><span>${hasData ? `${fmtInt(library.radarr?.queue || 0)} / ${fmtInt(library.sonarr?.queue || 0)}` : '—'}</span></div>
        </div>
      </div>
    </div>

    <div class="panel flush section">
      <div class="panel-head"><h2>Novedades en Radarr y Sonarr</h2><span class="right tiny faint">últimos cambios de biblioteca</span></div>
      <div class="panel-body flush">${recent || empty('Sin datos de Radarr/Sonarr')}</div>
    </div>
  `;
}
