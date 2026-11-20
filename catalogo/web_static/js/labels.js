const DOWNLOAD = {
  descargando: { label: 'Descargando', tone: 'info' },
  sembrando: { label: 'Descargado', tone: 'ok' },
  pausado: { label: 'Pausado', tone: 'warn' },
  error: { label: 'Error', tone: 'bad' },
  sin_descarga: { label: 'Sin descarga', tone: 'muted' },
};

const PUBLICATION = {
  publicado: { label: 'En biblioteca', tone: 'ok' },
  pendiente: { label: 'Por añadir', tone: 'info' },
  sin_fuente: { label: 'Sin fuente', tone: 'muted' },
};

const JOB_STATE = {
  queued: { label: 'En cola', tone: 'warn' },
  running: { label: 'Ejecutando', tone: 'info' },
  ok: { label: 'Completada', tone: 'ok' },
  error: { label: 'Error', tone: 'bad' },
  cancelled: { label: 'Cancelada', tone: 'muted' },
};

export const JOB_LABELS = {
  health: 'Verificar salud',
  'sync-sources': 'Buscar fuentes',
  'rss-sync': 'Novedades RSS',
  backfill: 'Fondo antiguo',
  'export-strm': 'Exportar .strm',
  'qbit-sync': 'Sincronizar qBittorrent',
  repair: 'Reparar errores',
  'clean-broken': 'Limpiar torrents rotos',
  'move-completed': 'Mover a 1fichier',
  abandon: 'Abandonar descargas',
  reseed: 'Reponer borrados',
  evict: 'Liberar espacio',
  seeding: 'Normalizar siembra',
  'prune-library': 'Podar biblioteca',
  'sync-library': 'Sincronizar biblioteca',
  metadata: 'Actualizar metadatos',
  monitor: 'Buscar seguimiento',
  'debrid-check': 'Comprobar debrid',
  'qbit-limits': 'Límites qBittorrent',
  'cleanup-leases': 'Limpiar leases',
  'publish-now': 'Añadir a la biblioteca',
  'download-now': 'Forzar descarga',
  'sync-pending': 'Buscar fuentes pendientes',
  'worker-cycle': 'Ciclo del worker',
  bridge: 'Puente a Radarr/Sonarr',
  'restore-strm': 'Restaurar .strm',
};

export const EVENT_LABELS = {
  'web.add_movie': 'Alta de película',
  'web.add_series': 'Alta de serie',
  'web.ondemand_acquire': 'Añadir a la biblioteca / forzar descarga',
  'web.ondemand_publish': 'Añadir a la biblioteca',
  'web.ondemand_download': 'Descarga forzada',
  'web.ondemand_limit': 'Cambio de límite',
  'web.manual_search': 'Búsqueda manual de fuentes',
  'web.collection_add': 'Añadir colección',
  'web.title_monitor': 'Seguimiento de título',
  'web.title_library': 'Añadir título a la biblioteca',
  'web.series_library': 'Añadir serie a la biblioteca',
  'web.library_remove': 'Quitar de la biblioteca',
  'web.series_unpublish': 'Quitar serie de la biblioteca',
  'web.sync_pending': 'Búsqueda de pendientes',
  'web.indexer_toggle': 'Pausar o activar indexer',
  'web.job': 'Tarea lanzada',
  'web.job_cancel': 'Tarea cancelada',
  'web.qbit_action': 'Acción en qBittorrent',
  'web.tracker_block': 'Bloqueo de tracker',
  'web.title_sync': 'Búsqueda de fuentes',
  'web.qbit_limits': 'Límites de qBittorrent',
  'web.settings_save': 'Configuración guardada',
  'web.integration_test': 'Prueba de integración',
  'web.scheduler_save': 'Tarea programada actualizada',
  'web.scheduler_run': 'Tarea programada lanzada',
};

export const CAPABILITY_LABELS = {
  prowlarr: 'Prowlarr',
  jackett: 'Jackett',
  tmdb: 'TMDB',
  realdebrid: 'Real-Debrid (avanzado)',
  play_service: 'Reproductor',
  qbittorrent: 'qBittorrent',
  jellyfin: 'Jellyfin',
  radarr: 'Radarr',
  sonarr: 'Sonarr',
};

export const SERVICE_LABELS = {
  tod: 'TOD (servicio único)',
  'catalogo-web': 'Panel web (obsoleto)',
  'catalogo-proxy': 'Pasarela de reproducción',
  'catalogo-worker': 'Worker',
  'catalogo-rss': 'RSS',
  'catalogo-backfill': 'Fondo antiguo',
  'catalogo-bridge': 'Puente a Radarr/Sonarr',
  'catalogo-restore': 'Restauración',
  'catalogo-leases': 'Leases de Jellyfin',
  'catalogo-qbit-limits': 'Límites de qBittorrent',
  qbittorrent: 'qBittorrent',
  prowlarr: 'Prowlarr',
  jackett: 'Jackett',
  jellyfin: 'Jellyfin',
};

export function downloadStatus(item) {
  if (!item) return DOWNLOAD.sin_descarga;
  if (item.kind === 'series' && item.download_counts) {
    const counts = item.download_counts;
    if (counts.descargando) return { label: 'Descargando', tone: 'info' };
    if (counts.pausado) return { label: 'Pausado', tone: 'warn' };
    if (counts.error) return { label: 'Error', tone: 'bad' };
    if (counts.descargado) {
      return {
        label: `${counts.descargado}/${item.episodes || 0} descargados`,
        tone: 'ok',
      };
    }
    return DOWNLOAD.sin_descarga;
  }
  return DOWNLOAD[item.download] || DOWNLOAD.sin_descarga;
}

export function publicationStatus(item) {
  if (!item) return PUBLICATION.sin_fuente;
  if (item.kind === 'series' && item.library_episodes !== undefined) {
    if (item.library_episodes > 0) {
      return {
        label: `${item.library_episodes}/${item.episodes || 0} en biblioteca`,
        tone: 'ok',
      };
    }
    if (item.publication === 'pendiente') return PUBLICATION.pendiente;
    return PUBLICATION.sin_fuente;
  }
  return PUBLICATION[item.publication] || PUBLICATION.sin_fuente;
}

export function jobState(state) {
  return JOB_STATE[state] || { label: state || '—', tone: 'muted' };
}

export function jobLabel(kind) {
  return JOB_LABELS[kind] || kind || 'Tarea';
}

export function eventLabel(kind) {
  return EVENT_LABELS[kind] || kind || 'Evento';
}

export function capabilityLabel(name) {
  return CAPABILITY_LABELS[name] || name;
}

export function qbitState(rawValue) {
  const raw = String(rawValue || '').toLowerCase();
  if (!raw) return { label: '—', tone: 'muted' };
  if (raw.includes('missing') || raw.includes('error')) return { label: 'Error', tone: 'bad' };
  if (raw.includes('paused') || raw.includes('stopped')) return { label: 'Pausado', tone: 'warn' };
  if (raw.includes('stalledup')) return { label: 'Sembrando (sin pares)', tone: 'warn' };
  if (raw.includes('stalleddl')) return { label: 'Descargando (sin pares)', tone: 'warn' };
  if (raw.includes('forcedup')) return { label: 'Sembrando (forzado)', tone: 'ok' };
  if (raw.includes('forceddl')) return { label: 'Descargando (forzado)', tone: 'info' };
  if (raw.includes('up') || raw.includes('seed') || raw.includes('upload')) return { label: 'Sembrando', tone: 'ok' };
  if (raw.includes('dl') || raw.includes('download') || raw.includes('meta')) return { label: 'Descargando', tone: 'info' };
  if (raw.includes('queued')) return { label: 'En cola', tone: 'muted' };
  return { label: rawValue, tone: 'muted' };
}

export function availabilityStatus(value) {
  const map = {
    local: { label: 'Local', tone: 'ok' },
    library: { label: 'Biblioteca', tone: 'ok' },
    debrid: { label: 'Debrid', tone: 'info' },
    cloud: { label: '1fichier', tone: 'info' },
    alive: { label: 'Fuente viva', tone: 'info' },
    unverified: { label: 'Sin verificar', tone: 'warn' },
    none: { label: 'Sin fuente', tone: 'muted' },
    downloading: { label: 'Descargando', tone: 'info' },
  };
  return map[value] || { label: value || '—', tone: 'muted' };
}

export function healthStatus(value) {
  const map = {
    alive: { label: 'Vivo', tone: 'ok' },
    stale: { label: 'Viejo', tone: 'warn' },
    dead: { label: 'Muerto', tone: 'bad' },
    blocked: { label: 'Bloqueado', tone: 'bad' },
    unknown: { label: 'Sin verificar', tone: 'muted' },
  };
  return map[value] || map.unknown;
}

export const TASK_CATEGORIES = [
  { key: 'captura', label: 'Captura y fuentes' },
  { key: 'biblioteca', label: 'Biblioteca' },
  { key: 'siembra', label: 'Descargas y siembra' },
  { key: 'sistema', label: 'Sistema' },
];

export const TASKS = [
  {
    kind: 'health', title: 'Verificar salud', category: 'captura',
    desc: 'Consulta Prowlarr en vivo y actualiza seeders, peers y estado.',
    fields: [
      { name: 'limit', label: 'Releases', type: 'number', value: 25, min: 1 },
      { name: 'delay', label: 'Pausa (s)', type: 'number', value: 1, step: 0.1 },
      { name: 'tracker', label: 'Tracker (opcional)', type: 'text' },
      { name: 'key', label: 'Clave (opcional)', type: 'text' },
      { name: 'dry_run', label: 'Solo previsualizar', type: 'checkbox', value: false },
    ],
  },
  {
    kind: 'sync-sources', title: 'Buscar fuentes', category: 'captura',
    desc: 'Busca releases en Prowlarr y Jackett para títulos fríos.',
    fields: [
      { name: 'year', label: 'Año', type: 'number', value: 2026, min: 1900, max: 2100 },
      { name: 'kind', label: 'Tipo', type: 'select', value: 'movie', options: [['movie', 'Películas'], ['series', 'Series']] },
      { name: 'limit', label: 'Títulos', type: 'number', value: 10, min: 1 },
      { name: 'delay', label: 'Pausa (s)', type: 'number', value: 8, step: 0.5 },
      { name: 'min_seeders', label: 'Mín. seeds', type: 'number', value: 1, min: 0 },
      { name: 'dry_run', label: 'Solo previsualizar', type: 'checkbox', value: false },
    ],
  },
  {
    kind: 'rss-sync', title: 'Novedades RSS', category: 'captura',
    desc: 'Ingesta lo último de Prowlarr/Jackett y publica seguimientos encontrados.',
    fields: [
      { name: 'limit', label: 'Resultados', type: 'number', value: 200, min: 1 },
      { name: 'delay', label: 'Pausa (s)', type: 'number', value: 0.3, step: 0.1 },
      { name: 'export', label: 'Exportar .strm', type: 'checkbox', value: true },
      { name: 'dry_run', label: 'Solo previsualizar', type: 'checkbox', value: false },
    ],
  },
  {
    kind: 'backfill', title: 'Fondo antiguo', category: 'captura',
    desc: 'Elige el año con más pendientes y captura un lote de 250.',
    fields: [
      { name: 'limit', label: 'Títulos', type: 'number', value: 250, min: 1 },
      { name: 'delay', label: 'Pausa (s)', type: 'number', value: 8, step: 0.5 },
      { name: 'years', label: 'Años (coma)', type: 'text', value: '2026,2025,2024,2023,2022,2021,2020' },
      { name: 'export', label: 'Exportar .strm', type: 'checkbox', value: true },
      { name: 'dry_run', label: 'Solo previsualizar', type: 'checkbox', value: true },
    ],
  },
  {
    kind: 'export-strm', title: 'Exportar .strm', category: 'biblioteca',
    desc: 'Genera los ficheros estables de Jellyfin.',
    fields: [
      { name: 'year', label: 'Año (vacío = todo)', type: 'number', value: 2026, min: 1900 },
      { name: 'kind', label: 'Tipo', type: 'select', value: 'movie', options: [['', 'Todo'], ['movie', 'Películas'], ['series', 'Series']] },
      { name: 'limit', label: 'Límite', type: 'number', value: 0, min: 0 },
      { name: 'min_seeders', label: 'Mín. seeds', type: 'number', value: 1, min: 0 },
      { name: 'prune', label: 'Podar .strm obsoletos', type: 'checkbox', value: false },
    ],
    warning: 'La poda solo se permite en una exportación completa (sin año ni límite).',
  },
  {
    kind: 'metadata', title: 'Actualizar metadatos', category: 'biblioteca',
    desc: 'Rellena póster, sinopsis y nota desde TMDB para el catálogo.',
    fields: [
      { name: 'limit', label: 'Títulos (0 = todos)', type: 'number', value: 0, min: 0 },
      { name: 'workers', label: 'Peticiones simultáneas', type: 'number', value: 6, min: 1, max: 16 },
      { name: 'delay', label: 'Pausa (s)', type: 'number', value: 0.08, step: 0.01 },
      { name: 'force', label: 'Revisar también los ya rellenados', type: 'checkbox', value: false },
    ],
  },
  {
    kind: 'monitor', title: 'Buscar seguimiento', category: 'biblioteca',
    desc: 'Busca títulos ya estrenados o emitidos. Los futuros esperan su fecha.',
    fields: [
      { name: 'limit', label: 'Títulos', type: 'number', value: 50, min: 1 },
      { name: 'delay', label: 'Pausa (s)', type: 'number', value: 2, step: 0.5 },
    ],
  },
  {
    kind: 'sync-library', title: 'Sincronizar biblioteca', category: 'biblioteca',
    desc: 'Importa library_files desde Radarr/Sonarr.',
    fields: [],
  },
  {
    kind: 'prune-library', title: 'Podar biblioteca', category: 'biblioteca',
    desc: 'Borra .strm de títulos que ya están en Radarr/Sonarr.',
    fields: [
      { name: 'execute', label: 'Ejecutar de verdad (no dry-run)', type: 'checkbox', value: false },
    ],
    danger: true,
  },
  {
    kind: 'move-completed', title: 'Mover a 1fichier', category: 'biblioteca',
    desc: 'Mueve privados completos e inactivos a la nube.',
    fields: [
      { name: 'target', label: 'Destino', type: 'text', value: '/mnt/fichier/descargas' },
      { name: 'limit', label: 'Torrents', type: 'number', value: 10, min: 1 },
      { name: 'grace', label: 'Gracia (s)', type: 'number', value: 120, min: 0 },
      { name: 'execute', label: 'Ejecutar de verdad (no dry-run)', type: 'checkbox', value: false },
    ],
    danger: true,
  },
  {
    kind: 'qbit-sync', title: 'Sincronizar qBittorrent', category: 'siembra',
    desc: 'Indexa torrents, ficheros y vínculos con Radarr/Sonarr.',
    fields: [
      { name: 'check_exists', label: 'Comprobar ficheros', type: 'checkbox', value: true },
      { name: 'dry_run', label: 'Solo previsualizar', type: 'checkbox', value: false },
    ],
  },
  {
    kind: 'repair', title: 'Reparar errores', category: 'siembra',
    desc: 'Recheck y reanuda torrents con error local.',
    fields: [
      { name: 'limit', label: 'Torrents', type: 'number', value: 5, min: 1 },
      { name: 'execute', label: 'Ejecutar de verdad (no dry-run)', type: 'checkbox', value: false },
    ],
  },
  {
    kind: 'clean-broken', title: 'Limpiar rotos', category: 'siembra',
    desc: 'Borra torrents que el tracker ya no reconoce y sus .strm.',
    fields: [
      { name: 'limit', label: 'Torrents', type: 'number', value: 10, min: 1 },
      { name: 'execute', label: 'Ejecutar de verdad (no dry-run)', type: 'checkbox', value: false },
    ],
    danger: true,
  },
  {
    kind: 'abandon', title: 'Abandonar descargas', category: 'siembra',
    desc: 'Pausa o borra descargas por debajo del 5 % sin reproducción.',
    fields: [
      { name: 'limit', label: 'Torrents', type: 'number', value: 20, min: 1 },
      { name: 'min_age', label: 'Edad mínima (s)', type: 'number', value: 600, min: 0 },
      { name: 'allow_private_delete', label: 'Permitir borrar en privados', type: 'checkbox', value: false },
      { name: 'execute', label: 'Ejecutar de verdad (no dry-run)', type: 'checkbox', value: false },
    ],
    danger: true,
  },
  {
    kind: 'reseed', title: 'Reponer borrados', category: 'siembra',
    desc: 'Re-descarga compromisos que qBittorrent borró antes del mínimo.',
    fields: [
      { name: 'limit', label: 'Compromisos', type: 'number', value: 1, min: 1 },
      { name: 'execute', label: 'Ejecutar de verdad (no dry-run)', type: 'checkbox', value: false },
    ],
    danger: true,
  },
  {
    kind: 'evict', title: 'Liberar espacio', category: 'siembra',
    desc: 'Borra copias locales que ya cumplieron su siembra.',
    fields: [
      { name: 'limit', label: 'Compromisos', type: 'number', value: 10, min: 1 },
      { name: 'grace', label: 'Gracia (s)', type: 'number', value: 120, min: 0 },
      { name: 'execute', label: 'Ejecutar de verdad (no dry-run)', type: 'checkbox', value: false },
    ],
    danger: true,
  },
  {
    kind: 'seeding', title: 'Normalizar siembra', category: 'siembra',
    desc: 'Quita forceStart a torrents completos.',
    fields: [
      { name: 'limit', label: 'Torrents', type: 'number', value: 50, min: 1 },
      { name: 'execute', label: 'Ejecutar de verdad (no dry-run)', type: 'checkbox', value: false },
    ],
  },
  {
    kind: 'cleanup-leases', title: 'Limpiar leases', category: 'siembra',
    desc: 'Elimina leases de reproducción con más de una hora.',
    fields: [],
  },
  {
    kind: 'qbit-limits', title: 'Límites qBittorrent', category: 'sistema',
    desc: 'Aplica límites de descarga por categoría.',
    fields: [
      { name: 'radarr_kbps', label: 'Radarr KiB/s', type: 'number', value: 2500, min: 0 },
      { name: 'sonarr_kbps', label: 'Sonarr KiB/s', type: 'number', value: 2500, min: 0 },
      { name: 'ondemand_kbps', label: 'On-demand KiB/s', type: 'number', value: 0, min: 0 },
      { name: 'dry_run', label: 'Solo previsualizar', type: 'checkbox', value: false },
    ],
  },
  {
    kind: 'debrid-check', title: 'Comprobar debrid', category: 'sistema',
    desc: 'Prueba caché de Real-Debrid para releases públicos.',
    fields: [
      { name: 'limit', label: 'Releases', type: 'number', value: 10, min: 1 },
      { name: 'delay', label: 'Pausa (s)', type: 'number', value: 1, step: 0.1 },
      { name: 'dry_run', label: 'Solo previsualizar', type: 'checkbox', value: true },
    ],
  },
];

export function taskDef(kind) {
  return TASKS.find((task) => task.kind === kind);
}
