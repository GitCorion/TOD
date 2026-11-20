import { api } from '../api.js';
import { esc, fmtBytes, fmtDate, fmtInt, fmtRelative } from '../format.js';
import { capabilityLabel, downloadStatus, jobLabel, jobState } from '../labels.js';
import { state } from '../store.js';
import { $, badge, barPercent, empty, icon, poster, setHeader, status } from '../ui.js';

export async function renderDashboard({ signal }) {
  const data = await api('/api/ondemand/overview', { signal });
  const activity = state.activity || await api('/api/activity', { signal }).catch(() => null);
  const jobsData = await api('/api/jobs?limit=6', { signal }).catch(() => ({ items: [] }));
  const preview = await api('/api/ondemand/catalog?publication=publicado&group=series&per_page=6&facets=0&sort=recent', { signal }).catch(() => ({ items: [] }));
  const setup = await api('/api/setup', { signal }).catch(() => null);
  const recentJobs = jobsData.items || [];
  const catalog = data.catalog || {};
  const movies = catalog.movies || {};
  const series = catalog.series || {};
  const downloads = data.downloads || {};
  const library = data.library || {};
  const disk = data.disk || {};
  const active = activity?.running || (activity?.active || [])[0] || null;
  const latest = activity?.latest || null;

  setHeader('Inicio', 'Biblioteca');

  const alerts = [];
  if (!library.ok) {
    const errors = library.errors || [];
    alerts.push(`Radarr/Sonarr no responden${errors.length ? ` (${errors.length} avisos)` : ''}. Comprueba que los servicios estén activos.`);
  }
  if (downloads.paused) alerts.push(`${fmtInt(downloads.paused)} descargas on-demand están pausadas.`);
  if (downloads.error) alerts.push(`${fmtInt(downloads.error)} descargas on-demand tienen error.`);
  const setupMissing = setup?.missing || [];

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
      : `<div class="job-active"><div class="info"><strong>Sin tareas recientes</strong><small>El catálogo está en reposo.</small></div><a class="btn btn-sm btn-ghost" href="#/jobs">Abrir tareas</a></div>`;

  $('#view').innerHTML = `
    <div class="row between" style="align-items:flex-start">
      <h2 style="margin:0;font-family:var(--serif);font-size:30px;font-weight:400;letter-spacing:-.025em">Buscar y añadir</h2>
      <a class="btn btn-ghost" href="#/catalog">Examinar catálogo</a>
    </div>
    <form class="row" data-form="quick-search" style="margin-top:12px">
      <input name="q" placeholder="Título de película o serie…" autocomplete="off" style="flex:1;min-width:220px" aria-label="Buscar título para añadir">
      <button class="btn btn-primary" type="submit">${icon('search')} Buscar</button>
    </form>

    ${setupMissing.length ? `<div class="panel pad section setup-card">
      <div class="row between" style="align-items:flex-start;gap:16px">
        <div>
          <strong>Termina de configurar TOD</strong>
          <p class="muted tiny" style="margin:4px 0 0">Conecta los servicios y revisa los directorios. Sin esta configuración no se pueden buscar fuentes ni reproducir.</p>
          <div class="row" style="margin-top:10px">${setupMissing.map((name) => badge(name === 'catalog_library' ? 'Biblioteca del catálogo' : capabilityLabel(name), 'mono')).join('')}</div>
        </div>
        <a class="btn btn-primary" href="#/settings">${icon('wrench', 'sm')} Configurar</a>
      </div>
    </div>` : ''}

    ${alerts.length ? `<div class="stack section">${alerts.map((text) => `<div class="notice">${esc(text)}</div>`).join('')}</div>` : ''}

    <div class="record-line section">
      <span><b>${fmtInt((movies.published || 0) + (series.published || 0))}</b> en biblioteca</span>
      <span><b>${fmtInt(catalog.episodes || 0)}</b> episodios</span>
      <span><b>${fmtInt(downloads.downloading || 0)}</b> descargando</span>
      <span><b>${fmtBytes(disk.free || 0)}</b> libres</span>
    </div>

    <div class="section">
      <div class="section-head"><h2>En el archivo</h2><a class="right tiny" href="#/catalog">Ver catálogo</a></div>
      ${(preview.items || []).length
        ? `<div class="poster-strip">
        ${(preview.items || []).map((item) => `<a class="strip-item" href="${item.kind === 'series' ? `#/series/${item.tmdb}` : `#/title/${item.id}`}">
          <span class="strip-poster">${poster(item.poster_path, item.title)}</span>
          <strong>${esc(item.title || '—')}</strong>
        </a>`).join('')}
      </div>`
        : empty('Sin títulos en el archivo', 'Añade una película o serie para empezar.', `<a class="btn btn-sm" href="#/add">Añadir contenido</a>`)}
    </div>

    <div class="grid grid-2 section">
      <div class="panel">
        <div class="panel-head"><h2>Actividad</h2>${badge(active ? 'en curso' : 'reposo', 'mono')}<a class="right tiny" href="#/jobs">Historial</a></div>
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
        <div class="panel-head"><h2>Últimas incorporaciones</h2></div>
        ${recent || empty('Sin datos de Radarr/Sonarr')}
      </div>
      <div class="panel">
        <div class="panel-head"><h2>Biblioteca Radarr / Sonarr</h2></div>
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
