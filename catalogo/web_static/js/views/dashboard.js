import { api } from '../api.js';
import { attentionCard, paintAttention } from '../attention.js';
import { esc, fmtBytes, fmtInt, fmtRelative } from '../format.js';
import { jobLabel, jobState } from '../labels.js';
import { state } from '../store.js';
import { $, barPercent, empty, icon, poster, setHeader } from '../ui.js';

function withTimeout(promise, ms, fallback) {
  let timer = null;
  const timeout = new Promise((resolve) => {
    timer = setTimeout(() => resolve(fallback), ms);
  });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}

function pctRatio(done, total) {
  if (!total) return 0;
  return Math.max(0, Math.min(100, Math.round((Number(done || 0) / Number(total)) * 100)));
}

export async function renderDashboard({ signal }) {
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
    api('/api/ondemand/recent?limit=12', { signal }).catch(() => ({ items: [] })),
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

  const moviesPub = Number(movies.published || 0);
  const moviesTotal = Number(movies.total || moviesPub || 0);
  const moviesPct = pctRatio(moviesPub, moviesTotal);

  const seriesPub = Number(series.published || 0);
  const seriesTotal = Number(series.total || seriesPub || 0);
  const seriesPct = pctRatio(seriesPub, seriesTotal);

  const usedPct = disk.total ? Math.round(((disk.total - (disk.free || 0)) / disk.total) * 100) : 0;
  const num = (value) => (hasData ? fmtInt(value) : '—');
  const bytes = (value) => (hasData ? fmtBytes(value) : '—');

  const navMovies = document.getElementById('navMovies');
  const navSeries = document.getElementById('navSeries');
  const navDownloads = document.getElementById('navDownloads');
  if (navMovies && hasData) navMovies.textContent = fmtInt(moviesTotal);
  if (navSeries && hasData) navSeries.textContent = fmtInt(seriesTotal);
  if (navDownloads && hasData) navDownloads.textContent = downloads.downloading ? fmtInt(downloads.downloading) : '';

  setHeader('Inicio');

  const recent = (library.recent || []).slice(0, 6).map((item) => {
    const meta = item.kind === 'movie'
      ? `Película, ${item.year || '—'}`
      : `T${String(item.season || 0).padStart(2, '0')}E${String(item.episode || 0).padStart(2, '0')}`;
    const href = item.kind === 'movie' && item.asset_id
      ? `#/title/${item.asset_id}`
      : item.kind === 'series' && item.series_tmdb
        ? `#/series/${item.series_tmdb}`
        : `#/movies?q=${encodeURIComponent(item.title || '')}`;
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
            <small>Última tarea: ${esc(jobState(latest.state).label)} · ${esc(fmtRelative(latest.finished_at || latest.created_at))}</small>
          </div>
          <a class="btn btn-sm btn-ghost" href="#/jobs">Tareas</a>
        </div>`
      : `<div class="job-active"><div class="info"><strong>Todo en calma</strong><small>Sin tareas ejecutándose.</small></div><a class="btn btn-sm btn-ghost" href="#/jobs">Tareas</a></div>`;

  $('#view').innerHTML = `
    <div class="section" style="margin-top:0">${attentionCard(attention, { compact: true })}</div>
    ${hasData ? '' : `<div class="notice section">No se pudo leer el resumen del servidor a tiempo. Los contadores se actualizarán al recargar.</div>`}

    <form class="home-quick-add section" data-form="quick-search">
      ${icon('search')}
      <input name="q" placeholder="Buscar película o serie para añadir a TOD (también acepta enlaces TMDB)…" autocomplete="off" aria-label="Añadir película o serie">
      <button class="btn btn-primary" type="submit">${icon('plus', 'sm')} Buscar y añadir</button>
    </form>

    <div class="home-readiness section">
      <a class="home-stat-card" href="#/movies">
        <div class="home-stat-top">
          <span class="home-stat-label">Películas listas</span>
          <span class="badge mono">${hasData ? `${moviesPct}%` : '—'}</span>
        </div>
        <div class="home-stat-val">${num(moviesPub)}<small>/ ${num(moviesTotal)}</small></div>
        ${barPercent(moviesPct, 'green')}
        <span class="home-stat-sub">${hasData ? `${fmtInt(movies.pending || 0)} pendientes · ver películas` : 'Cargando…'}</span>
      </a>

      <a class="home-stat-card" href="#/shows">
        <div class="home-stat-top">
          <span class="home-stat-label">Series listas</span>
          <span class="badge mono">${hasData ? `${seriesPct}%` : '—'}</span>
        </div>
        <div class="home-stat-val">${num(seriesPub)}<small>/ ${num(seriesTotal)}</small></div>
        ${barPercent(seriesPct, 'green')}
        <span class="home-stat-sub">${hasData ? `${fmtInt(catalog.episodes || 0)} episodios totales` : 'Cargando…'}</span>
      </a>

      <a class="home-stat-card" href="#/downloads">
        <div class="home-stat-top">
          <span class="home-stat-label">Descargas</span>
          ${downloads.error ? `<span class="status bad">${fmtInt(downloads.error)} error</span>` : ''}
        </div>
        <div class="home-stat-val">${num(downloads.downloading || 0)}<small>activas</small></div>
        ${barPercent(downloads.downloading ? 65 : 0, downloads.error ? 'bad' : '')}
        <span class="home-stat-sub">${hasData ? `${fmtInt(downloads.seeding || 0)} sembrando · ${fmtInt(downloads.paused || 0)} pausadas` : 'Sin datos de qBittorrent'}</span>
      </a>

      <a class="home-stat-card" href="#/system">
        <div class="home-stat-top">
          <span class="home-stat-label">Espacio libre</span>
          <span class="badge mono">${hasData ? `${usedPct}%` : '—'}</span>
        </div>
        <div class="home-stat-val">${bytes(disk.free || 0)}</div>
        ${barPercent(usedPct, usedPct >= 90 ? 'bad' : usedPct >= 78 ? 'warn' : 'green')}
        <span class="home-stat-sub">${hasData ? `de ${fmtBytes(disk.total || 0)} disponibles` : 'Sin datos del servidor'}</span>
      </a>
    </div>

    <div class="panel section">
      <div class="panel-head">
        <h2>Disponibles recientemente</h2>
        <div class="right row" style="gap:6px">
          <a class="btn btn-sm btn-ghost" href="#/movies">Películas</a>
          <a class="btn btn-sm btn-ghost" href="#/shows">Series</a>
        </div>
      </div>
      <div class="panel-body">
        ${(preview.items || []).length
          ? `<div class="poster-strip">
          ${(preview.items || []).slice(0, 6).map((item) => `<a class="strip-item" href="${item.kind === 'series' ? `#/series/${item.tmdb}` : `#/title/${item.id}`}">
            <span class="strip-poster">${poster(item.poster_path, item.title)}</span>
            <strong>${esc(item.title || '—')}</strong>
          </a>`).join('')}
        </div>`
          : empty('Tu biblioteca está vacía', 'Busca una película o serie arriba para empezar a disfrutarla.', `<a class="btn btn-sm btn-primary" href="#/add">Añadir título</a>`)}
      </div>
    </div>

    <div class="grid grid-2 section">
      <div class="panel flush">
        <div class="panel-head"><h2>Actividad reciente</h2><a class="right tiny faint" href="#/events">Ver eventos</a></div>
        <div class="panel-body" style="padding:0">
          ${recent ? `<div class="media-rows" style="border:0;border-radius:0">${recent}</div>` : empty('Sin actividad reciente', 'Las incorporaciones aparecerán aquí.')}
        </div>
      </div>

      <div class="panel">
        <div class="panel-head"><h2>Estado y tareas</h2><a class="right tiny faint" href="#/jobs">Abrir tareas</a></div>
        <div class="panel-body" style="padding-top:4px;padding-bottom:6px">
          ${activePanel}
        </div>
      </div>
    </div>
  `;
}
