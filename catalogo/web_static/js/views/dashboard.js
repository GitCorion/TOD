import { api } from '../api.js';
import { attentionCard } from '../attention.js';
import { esc, fmtBytes, fmtDate, fmtInt, fmtRelative } from '../format.js';
import { jobLabel, jobState } from '../labels.js';
import { state } from '../store.js';
import { $, badge, barPercent, empty, icon, poster, setHeader } from '../ui.js';

export async function renderDashboard({ signal }) {
  const [data, attention] = await Promise.all([
    api('/api/ondemand/overview', { signal }),
    api('/api/attention', { signal }).catch(() => null),
  ]);
  state.attention = attention;
  const activity = state.activity || await api('/api/activity', { signal }).catch(() => null);
  const jobsData = await api('/api/jobs?limit=30', { signal }).catch(() => ({ items: [] }));
  const preview = await api('/api/ondemand/catalog?publication=publicado&group=series&per_page=6&facets=0&sort=recent', { signal }).catch(() => ({ items: [] }));
  const routine = new Set(['attention-check', 'qbit-limits', 'worker-cycle']);
  const recentJobs = (jobsData.items || []).filter((job) => !routine.has(job.kind));
  const catalog = data.catalog || {};
  const movies = catalog.movies || {};
  const series = catalog.series || {};
  const downloads = data.downloads || {};
  const library = data.library || {};
  const disk = data.disk || {};
  const active = activity?.running || (activity?.active || [])[0] || null;
  const latest = recentJobs[0] || null;

  setHeader('Inicio', 'Contenido');

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
          <small>${esc(active.message || 'En curso')}, ${active.state === 'queued' ? 'en cola' : `${Math.round((active.progress || 0) * 100)} %`}</small>
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

    <div class="section">
      <h2 style="margin:0;font-family:var(--serif);font-size:30px;font-weight:400;letter-spacing:-.025em">Añadir desde TMDB</h2>
      <form class="row" data-form="quick-search" style="margin-top:12px">
        <input name="q" placeholder="Película o serie…" autocomplete="off" style="flex:1;min-width:220px" aria-label="Buscar película o serie en TMDB">
        <button class="btn btn-primary" type="submit">${icon('search')} Buscar</button>
      </form>
    </div>

    <div class="record-line section">
      <span><b>${fmtInt((movies.published || 0) + (series.published || 0))}</b> en Jellyfin</span>
      <span><b>${fmtInt(catalog.episodes || 0)}</b> episodios</span>
      <span><b>${fmtInt(downloads.downloading || 0)}</b> descargando</span>
      <span><b>${fmtBytes(disk.free || 0)}</b> libres</span>
    </div>

    <div class="section">
      <div class="section-head"><h2>En Jellyfin</h2><a class="right tiny" href="#/catalog">Ver todo</a></div>
      ${(preview.items || []).length
        ? `<div class="poster-strip">
        ${(preview.items || []).map((item) => `<a class="strip-item" href="${item.kind === 'series' ? `#/series/${item.tmdb}` : `#/title/${item.id}`}">
          <span class="strip-poster">${poster(item.poster_path, item.title)}</span>
          <strong>${esc(item.title || '—')}</strong>
        </a>`).join('')}
      </div>`
        : empty('No hay títulos añadidos', 'Añade una película o serie para empezar.', `<a class="btn btn-sm" href="#/add">Añadir desde TMDB</a>`)}
    </div>

    <div class="grid grid-2 section">
      <div class="panel">
        <div class="panel-head"><h2>Actividad</h2>${badge(active ? 'en curso' : 'reposo', 'mono')}<a class="right tiny" href="#/jobs">Ver tareas</a></div>
        ${activePanel}
        ${recentJobs.length ? `<div class="panel-body" style="padding-top:6px;padding-bottom:8px">
          ${recentJobs.filter((job) => !active || job.id !== active.id).slice(0, 5).map((job) => `<div class="kv">
            <span><span class="status ${jobState(job.state).tone} no-dot">${esc(jobState(job.state).label)}</span> ${esc(jobLabel(job.kind))}</span>
            <span class="tiny faint">${esc(fmtRelative(job.finished_at || job.created_at))}</span>
          </div>`).join('')}
        </div>` : ''}
      </div>
      <div class="panel">
        <div class="panel-head"><h2>Descargas</h2><a class="right tiny" href="#/downloads">Ver todas</a></div>
        <div class="panel-body" style="padding-top:8px;padding-bottom:8px">
          <div class="kv"><span>Descargando</span><span>${fmtInt(downloads.downloading || 0)}</span></div>
          <div class="kv"><span>Sembrando</span><span>${fmtInt(downloads.seeding || 0)}</span></div>
          <div class="kv"><span>Pausadas</span><span>${fmtInt(downloads.paused || 0)}</span></div>
          <div class="kv"><span>Con error</span><span>${fmtInt(downloads.error || 0)}</span></div>
        </div>
      </div>
    </div>

    <div class="grid grid-2 section">
      <div class="panel flush">
        <div class="panel-head"><h2>Novedades en Radarr y Sonarr</h2></div>
        ${recent || empty('Sin datos de Radarr/Sonarr')}
      </div>
      <div class="panel">
        <div class="panel-head"><h2>Radarr y Sonarr</h2><span class="right tiny faint">solo lectura</span></div>
        <div class="panel-body" style="padding-top:8px;padding-bottom:8px">
          <div class="kv"><span>Películas con fichero</span><span>${fmtInt(library.radarr?.movies_available || 0)}</span></div>
          <div class="kv"><span>Series con fichero</span><span>${fmtInt(library.sonarr?.series_available || 0)}</span></div>
          <div class="kv"><span>Episodios</span><span>${fmtInt(library.sonarr?.episodes_available || 0)} de ${fmtInt(library.sonarr?.episodes_total || 0)}</span></div>
          <div class="kv"><span>Cola</span><span>${fmtInt(library.radarr?.queue || 0)} / ${fmtInt(library.sonarr?.queue || 0)}</span></div>
        </div>
      </div>
    </div>
  `;
}
