# Glosario de la interfaz

La interfaz adopta la terminología familiar de **Radarr** y **Sonarr** adaptada al
funcionamiento bajo demanda de **TOD** (reproducción mediante ficheros `.strm` en
Jellyfin sin descarga previa obligatoria).

Estos son los términos oficiales que deben aparecer en pantalla:

| En la interfaz | Qué significa | Nombre interno |
|---|---|---|
| **Disponible** | Tiene su `.strm` y fuente viva o fichero; se puede reproducir en Jellyfin. | `publicado` |
| **Pendiente de .strm** | Tiene fuente viva o fichero local, pero todavía falta generar su `.strm`. | `pendiente` |
| **Sin fuente** | No hay ningún release vivo ni fichero que se pueda reproducir. | `sin_fuente` |
| **Monitorizado** | TOD buscará releases automáticamente y generará el `.strm` cuando aparezcan. | `monitored` |
| **Sin estrenar** | El episodio o película aún no se ha emitido/estrenado; no cuenta como faltante. | `available=false` |
| **En biblioteca** | El título forma parte de la biblioteca gestionada por TOD. | `library_added` |
| **En Radarr/Sonarr** | Existe un fichero real indexado desde Radarr o Sonarr (solo lectura). | `has_file` |
| **Release** | Un candidato torrent concreto (en tablas de búsqueda y detalle). | `release` |
| **Fuente** | Indicador general de disponibilidad reproducible (*Sin fuente*, *Fuente viva*). | `source` |
| **Buscados** | Sección estilo *Wanted* con *Faltantes*, *Mejorables* y *Próximos*. | `monitor` |
| **Cola** | Descargas y siembras activas en qBittorrent. | `downloads` |
| **Blocklist** | Releases bloqueados manualmente o por fallos para no volver a usarlos. | `blocklist` |
| **Carpeta de los .strm** | Carpeta donde TOD escribe los ficheros que lee Jellyfin. | `catalog_library` |

## Búsqueda y acciones sobre releases

- **Búsqueda automática**: busca e indexa releases según el perfil del título.
- **Búsqueda interactiva**: abre el listado de releases para elegir manualmente.
- **Usar por defecto**: fija un release concreto como preferido para el `.strm`.
- **Preparar**: descarga/pre-cachea el release en qBittorrent para reproducirlo.
- **Forzar**: prepara un release aunque esté rechazado por el perfil de calidad.
- **Añadir a Blocklist**: veta un release para que TOD no lo use automáticamente.

## Trackers

- **Descargar** solo permite o bloquea descargas nuevas de ese tracker.
- **Bloquear** marca sus releases como no disponibles y permite borrar sus
  `.strm`. El principal solo se borra si el título se queda sin otra fuente ni
  fichero local.

## Reglas de escritura

- No usar **publicar**, **publicación**, **publicado**, **catalogar**,
  **puntero virtual** ni **seguimiento** en la interfaz.
- Para la monitorización automática, usar **Monitorizado**, **Monitorizar** y
  **Dejar de monitorizar**. Los **especiales** se cuentan aparte y no como
  episodios faltantes.
- En tareas y ajustes, evitar `dry-run` en crudo; usar **Modo simulación (sin
  cambios)** y **Aplicar cambios reales**.
- Jellyfin, Radarr, Sonarr, qBittorrent, Prowlarr, Jackett y TMDB se escriben
  con su mayúscula oficial.
