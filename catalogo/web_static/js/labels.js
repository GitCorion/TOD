const CURRENT_YEAR = new Date().getFullYear();

const DOWNLOAD = {
  descargando: { label: 'Descargando', tone: 'info' },
  sembrando: { label: 'Descargado', tone: 'ok' },
  pausado: { label: 'Pausado', tone: 'warn' },
  error: { label: 'Error', tone: 'bad' },
  sin_descarga: { label: 'Sin descarga', tone: 'muted' },
};

const PUBLICATION = {
  publicado: { label: 'Disponible', tone: 'ok' },
  pendiente: { label: 'Pendiente', tone: 'info' },
  sin_fuente: { label: 'Sin fuente', tone: 'muted' },
};

/**
 * El estado que ve el usuario. Es el único eje principal: el resto
 * (origen, Radarr/Sonarr, seguimiento, descarga) son datos secundarios.
 */
export const WATCH = {
  ready: { label: 'Disponible', tone: 'ok' },
  pending: { label: 'Pendiente', tone: 'info' },
  monitoring: { label: 'En seguimiento', tone: 'info' },
  unavailable: { label: 'Sin fuente', tone: 'muted' },
};

export function watchState(item) {
  if (!item) return WATCH.unavailable;
  if (item.published) return WATCH.ready;
  if (item.publication === 'pendiente' || item.downloaded) return WATCH.pending;
  if (item.monitored) return WATCH.monitoring;
  return WATCH.unavailable;
}

const ORIGIN = {
  tmdb: 'TMDB',
  radarr: 'Radarr',
  sonarr: 'Sonarr',
  rss: 'Novedades RSS',
  import: 'Importación',
  link: 'Enlace pegado',
  torrent: 'Torrent manual',
  backfill: 'Fondo antiguo',
  qbit: 'qBittorrent',
  collection: 'Saga',
  auto: 'Seguimiento',
  legacy: 'Catálogo anterior',
  mixed: 'Varios orígenes',
};

export function originLabel(value) {
  return ORIGIN[value] || 'Sin procedencia';
}

const JOB_STATE = {
  queued: { label: 'En cola', tone: 'warn' },
  running: { label: 'Ejecutando', tone: 'info' },
  ok: { label: 'Completada', tone: 'ok' },
  error: { label: 'Error', tone: 'bad' },
  cancelled: { label: 'Cancelada', tone: 'muted' },
};

export const JOB_LABELS = {
  health: 'Verificar salud',
  'attention-check': 'Comprobación de estado',
  'sync-sources': 'Buscar fuentes',
  'rss-sync': 'Novedades RSS',
  backfill: 'Fondo antiguo',
  'export-strm': 'Exportar .strm',
  'qbit-sync': 'Sincronizar qBittorrent',
  repair: 'Reparar errores',
  'clean-broken': 'Limpiar torrents rotos',
  'move-completed': 'Mover a archivo',
  abandon: 'Abandonar descargas',
  reseed: 'Reponer borrados',
  evict: 'Liberar espacio',
  seeding: 'Ajustar siembra',
  'prune-library': 'Limpiar Radarr/Sonarr',
  'sync-library': 'Sincronizar Radarr/Sonarr',
  'arr-mirror': 'Espejo Radarr/Sonarr',
  metadata: 'Actualizar metadatos',
  monitor: 'Buscar fuentes en seguimiento',
  'fallback-sweep': 'Respaldo de Radarr/Sonarr',
  'qbit-limits': 'Límites qBittorrent',
  'cleanup-leases': 'Liberar reproducciones',
  'publish-now': 'Añadir',
  'download-now': 'Descargar ahora',
  'sync-pending': 'Buscar fuentes pendientes',
  'worker-cycle': 'Mantenimiento',
  bridge: 'Enviar a Radarr/Sonarr',
  'restore-strm': 'Reconciliar .strm',
};

export const EVENT_LABELS = {
  'web.add_movie': 'Alta de película',
  'web.add_series': 'Alta de serie',
  'web.ondemand_acquire': 'Añadir / descargar',
  'web.ondemand_publish': 'Añadir',
  'web.ondemand_download': 'Descargar ahora',
  'web.intake_import': 'Importación de lista',
  'web.ondemand_limit': 'Cambio de límite',
  'web.manual_search': 'Búsqueda manual de fuentes',
  'web.collection_add': 'Añadir colección',
  'web.title_monitor': 'Título en seguimiento',
  'web.title_library': 'Añadir título',
  'web.series_library': 'Añadir serie',
  'web.library_remove': 'Quitar de TOD',
  'web.series_unpublish': 'Quitar serie de TOD',
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
  'web.seed_settings': 'Ajustes de siembra',
  'web.seed_policy': 'Política de siembra',
  'web.seed_finish': 'Compromiso de siembra cerrado',
  'web.language': 'Preferencia de idioma',
  'web.attention_check': 'Comprobación de estado',
  'web.set_target': 'Versión fijada',
  'web.release_health': 'Salud de la versión',
  'web.intake_torrent': 'Torrent manual añadido',
  'web.prepare': 'Preparación de reproducción',
};

export const CAPABILITY_LABELS = {
  prowlarr: 'Prowlarr',
  jackett: 'Jackett',
  tmdb: 'TMDB',
  play_service: 'Reproductor',
  qbittorrent: 'qBittorrent',
  jellyfin: 'Jellyfin',
  radarr: 'Radarr',
  sonarr: 'Sonarr',
};

export const SERVICE_LABELS = {
  tod: 'TOD',
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
        label: `${item.library_episodes}/${item.episodes || 0} disponibles`,
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

function humanizeKey(kind) {
  const text = String(kind || '').replace(/^web\./, '').replace(/[._]+/g, ' ').trim();
  if (!text) return '';
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export function eventLabel(kind) {
  return EVENT_LABELS[kind] || humanizeKey(kind) || 'Evento';
}

export function capabilityLabel(name) {
  return CAPABILITY_LABELS[name] || name;
}

export function qbitState(rawValue) {
  const raw = String(rawValue || '').toLowerCase();
  if (!raw) return { label: '—', tone: 'muted' };
  if (raw.includes('missing') || raw.includes('error')) return { label: 'Con error', tone: 'bad' };
  if (raw.includes('paused') || raw.includes('stopped')) return { label: 'Pausado', tone: 'warn' };
  if (raw.includes('queuedup')) return { label: 'En cola de siembra', tone: 'muted' };
  if (raw.includes('queued')) return { label: 'En cola', tone: 'muted' };
  if (raw.includes('moving')) return { label: 'Moviendo a archivo', tone: 'warn' };
  if (raw.includes('checking')) return { label: 'Comprobando', tone: 'info' };
  if (raw.includes('stalledup')) return { label: 'Sembrando (sin pares)', tone: 'warn' };
  if (raw.includes('stalleddl')) return { label: 'Descargando (sin pares)', tone: 'warn' };
  if (raw.includes('forcedup')) return { label: 'Sembrando (forzado)', tone: 'ok' };
  if (raw.includes('forceddl')) return { label: 'Descargando (forzado)', tone: 'info' };
  if (raw.includes('up') || raw.includes('seed') || raw.includes('upload')) return { label: 'Sembrando', tone: 'ok' };
  if (raw.includes('dl') || raw.includes('download') || raw.includes('meta')) return { label: 'Descargando', tone: 'info' };
  return { label: rawValue, tone: 'muted' };
}

export function availabilityStatus(value) {
  const map = {
    local: { label: 'Local', tone: 'ok' },
    library: { label: 'Radarr/Sonarr', tone: 'ok' },
    cloud: { label: 'archivo', tone: 'info' },
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
  { key: 'biblioteca', label: 'Radarr/Sonarr' },
  { key: 'siembra', label: 'Descargas y siembra' },
  { key: 'sistema', label: 'Sistema' },
];

export const TASKS = [
  {
    kind: 'health', title: 'Verificar salud', category: 'captura',
    desc: 'Consulta Prowlarr en vivo y actualiza seeders, peers y estado.',
    fields: [
      { name: 'limit', label: 'Versiones', type: 'number', value: 25, min: 1 },
      { name: 'delay', label: 'Pausa (s)', type: 'number', value: 1, step: 0.1 },
      { name: 'tracker', label: 'Tracker (opcional)', type: 'text' },
      { name: 'key', label: 'Clave (opcional)', type: 'text' },
      { name: 'dry_run', label: 'Solo previsualizar', type: 'checkbox', value: false },
    ],
  },
  {
    kind: 'sync-sources', title: 'Buscar fuentes', category: 'captura',
    desc: 'Busca versiones en Prowlarr y Jackett para títulos sin fuente.',
    fields: [
      { name: 'year', label: 'Año', type: 'number', value: CURRENT_YEAR, min: 1900, max: 2100 },
      { name: 'kind', label: 'Tipo', type: 'select', value: 'movie', options: [['movie', 'Películas'], ['series', 'Series']] },
      { name: 'limit', label: 'Títulos', type: 'number', value: 10, min: 1 },
      { name: 'delay', label: 'Pausa (s)', type: 'number', value: 8, step: 0.5 },
      { name: 'min_seeders', label: 'Mín. seeds', type: 'number', value: 1, min: 0 },
      { name: 'dry_run', label: 'Solo previsualizar', type: 'checkbox', value: false },
    ],
  },
  {
    kind: 'rss-sync', title: 'Novedades RSS', category: 'captura',
    desc: 'Ingesta lo último de Prowlarr/Jackett y añade los seguimientos encontrados.',
    fields: [
      { name: 'limit', label: 'Resultados', type: 'number', value: 200, min: 1 },
      { name: 'delay', label: 'Pausa (s)', type: 'number', value: 0.2, step: 0.1 },
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
      { name: 'years', label: 'Años (coma)', type: 'text', value: [CURRENT_YEAR, CURRENT_YEAR-1, CURRENT_YEAR-2, CURRENT_YEAR-3, CURRENT_YEAR-4, CURRENT_YEAR-5, CURRENT_YEAR-6].join(',') },
      { name: 'export', label: 'Exportar .strm', type: 'checkbox', value: true },
      { name: 'dry_run', label: 'Solo previsualizar', type: 'checkbox', value: true },
    ],
  },
  {
    kind: 'export-strm', title: 'Crear accesos', category: 'biblioteca',
    desc: 'Genera los ficheros estables de acceso.',
    fields: [
      { name: 'year', label: 'Año (vacío = todo)', type: 'number', value: CURRENT_YEAR, min: 1900 },
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
    kind: 'monitor', title: 'Buscar fuentes en seguimiento', category: 'biblioteca',
    desc: 'Busca títulos ya estrenados o emitidos. Los futuros esperan su fecha.',
    fields: [
      { name: 'limit', label: 'Títulos', type: 'number', value: 50, min: 1 },
      { name: 'delay', label: 'Pausa (s)', type: 'number', value: 2, step: 0.5 },
    ],
  },
  {
    kind: 'sync-library', title: 'Sincronizar Radarr/Sonarr', category: 'biblioteca',
    desc: 'Importa library_files desde Radarr/Sonarr.',
    fields: [],
  },
  {
    kind: 'arr-mirror', title: 'Espejo Radarr/Sonarr', category: 'biblioteca',
    desc: 'Importa altas, busca fuentes y crea sus accesos.',
    fields: [
      { name: 'source_limit', label: 'Títulos a buscar', type: 'number', value: 50, min: 0 },
      { name: 'publish_limit', label: 'Títulos a preparar', type: 'number', value: 200, min: 0 },
      { name: 'delay', label: 'Pausa (s)', type: 'number', value: 0.2, step: 0.1 },
    ],
  },
  {
    kind: 'prune-library', title: 'Limpiar Radarr/Sonarr', category: 'biblioteca',
    desc: 'Borra .strm de títulos que ya están en Radarr/Sonarr.',
    fields: [
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
    kind: 'move-completed', title: 'Mover a archivo', category: 'siembra',
    desc: 'Mueve los torrents on-demand completados al archivo siguiendo sembrando.',
    fields: [
      { name: 'limit', label: 'Torrents', type: 'number', value: 10, min: 1 },
      { name: 'grace', label: 'Gracia (s)', type: 'number', value: 300, min: 0 },
      { name: 'target', label: 'Destino (vacío = configurado)', type: 'text' },
      { name: 'execute', label: 'Ejecutar de verdad (no dry-run)', type: 'checkbox', value: false },
    ],
    danger: true,
  },
  {
    kind: 'seeding', title: 'Mantener siembra mínima', category: 'siembra',
    desc: 'Fuerza la siembra hasta horas/ratio; después suelta y qBittorrent decide.',
    fields: [
      { name: 'limit', label: 'Torrents', type: 'number', value: 50, min: 1 },
      { name: 'dry_run', label: 'Solo previsualizar', type: 'checkbox', value: false },
    ],
  },
  {
    kind: 'cleanup-leases', title: 'Liberar reproducciones', category: 'siembra',
    desc: 'Cierra reproducciones inactivas de más de una hora.',
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
];

export function taskDef(kind) {
  return TASKS.find((task) => task.kind === kind);
}
