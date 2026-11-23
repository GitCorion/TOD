# Arquitectura

TOD es un único proceso Python con cuatro responsabilidades: web, API,
reproducción y scheduler. Comparten configuración, base de datos y clientes.

## Componentes

```text
catalogo/
├── web.py            SPA + API + rutas de reproducción
├── http.py           /play, /resolve, /sources, /status, búsqueda al vuelo
├── play_service.py   admisión, prepare, preflight y streaming
├── resolver.py       local_seed → library → torrent → unavailable
├── backend.py        qBittorrent de escritura, admisión y capacidad
├── scheduler.py      tareas periódicas internas
├── app_settings.py   ajustes tipados, secretos y recarga
├── log.py            niveles y formato de log del proceso
├── api_status.py     servicios, discos, indexers y salud para la API
├── attention.py      avisos agregados de configuración, rutas e integraciones
├── integrations.py   pruebas de conexión
├── arr.py            clientes Radarr/Sonarr (lectura y altas puntuales)
├── arr_mirror.py     espejo Radarr/Sonarr → catálogo
├── bridge.py         TOD → Radarr/Sonarr tras una descarga
├── intake.py         añadir, preflight, torrent manual y recepción
├── qbit_sync.py      inventario de qBittorrent en solo lectura
├── strm.py           generación de .strm multiversión
└── web_static/       SPA sin dependencias
```

## Flujo de una reproducción

1. Jellyfin lee un `.strm` y llama a `/play?key=...&download=0`.
2. TOD resuelve la clave: fichero local, biblioteca, debrid o torrent.
3. Si no hay fuente viva, `/play` busca en Prowlarr/Jackett y guarda los
   candidatos.
4. Si el torrent ya está preparado, sirve rangos con `206`.
5. Si está frío, un `GET` con `Range` autoriza el prepare; un escaneo sin
   `Range` sigue recibiendo `403`.
6. El scheduler sincroniza qBittorrent, espeja Radarr/Sonarr, busca fuentes,
   crea los `.strm` y devuelve a *arr lo descargado por TOD.

## Descubrimiento de trackers

TOD puede usar Prowlarr, Jackett o ambos. Si hay más de uno configurado,
consulta las dos fuentes.

- **Prowlarr**: TOD usa todos los indexers habilitados. De cada uno lee
  `name`, `language` y `privacy`; el idioma del indexer se usa para inferir el
  audio cuando el título no trae una etiqueta explícita.
- **Jackett**: consulta el indexer `all`; cada resultado incluye
  `<jackettindexer>` y `<type>`, así que TOD conoce el tracker real y su
  privacidad. No necesita Prowlarr para funcionar.
- No hay listas de trackers privadas ni credenciales en el repositorio.

## Radarr y Sonarr

TOD se integra con Radarr y Sonarr mediante sus APIs, configuradas con
`radarr_url` + `radarr_api_key` y `sonarr_url` + `sonarr_api_key`:

- biblioteca (`/movie`, `/series`, `/episode`, `/episodefile`),
- historial (`/history`) para mapear `DownloadId`,
- reescaneo (`/command`) tras el puente,
- cola (`/queue`) para el panel.

Los clientes están en `catalogo/arr.py`; los índices de biblioteca en
`library_index.py` y `library_sync.py`.

## Base de datos

SQLite en `var/tod.db` (`0600`, WAL; en Docker, `/config/tod.db`). Tablas
principales:

| Tabla | Contenido |
|---|---|
| `media_items` | películas y episodios con clave estable y `origin` |
| `intake_batches`, `intake_items` | lotes de entrada y estado de cada línea |
| `releases` | candidatos de Prowlarr/Jackett |
| `torrents`, `torrent_files` | estado real de qBittorrent |
| `library_files` | ficheros de Radarr/Sonarr vía API |
| `seed_commitments` | tiempo y ratio de seed |
| `play_intents`, `stream_leases` | reproducción y leases |
| `tracker_policies` | reglas por tracker |
| `jobs` | cola persistente y scheduler |
| `settings` | configuración web y secretos |
| `integration_health` | último estado y fallos consecutivos de cada integración |
| `events` | auditoría |

## API principal

| Ruta | Uso |
|---|---|
| `/api/overview`, `/api/activity` | panel |
| `/api/attention`, `/api/attention/check` | avisos de estado y comprobación bajo demanda |
| `/api/ondemand/*` | catálogo, añadir, descargar |
| `/api/ondemand/recent` | últimas incorporaciones ya publicadas |
| `/api/indexers` | indexers de Prowlarr y su estado |
| `/api/monitor/health` | salud de RSS, barrido e indexers |
| `/api/intake`, `/api/intake/preflight`, `/api/intake/import` | recepción, análisis y alta de listas |
| `/api/intake/torrent` | `.torrent` manual a qBittorrent |
| `/api/intake/batches/{id}` | detalle de un lote |
| `/api/settings` | leer y guardar configuración |
| `/api/integrations/{name}/test` | probar conexión |
| `/api/scheduler` | tareas y próxima ejecución |
| `/play`, `/resolve`, `/sources`, `/status` | reproducción |
| `/catalog/lookup` | resolver un `.strm` desde la app |

## Principios de diseño

- Un proceso, una configuración y un motor de reproducción.
- Los secretos viven en SQLite y se enmascaran en la API.
- Precedencia de configuración: base de datos > entorno > valores por defecto.
- Los cambios se aplican en caliente; las reproducciones activas conservan su
  instancia anterior.
- La procedencia es un dato, no una inferencia: `media_items.origin` se escribe
  en el alta y no se pisa al actualizar metadatos.
- El scheduler usa `jobs`, que serializa tareas y evita solapamientos.
- La lista del catálogo se calcula desde el espejo local (`library_files`),
  no con llamadas por episodio a Radarr/Sonarr. Se cachea 30 s y las acciones
  del usuario la invalidan.
- El inventario de `.strm` se cachea 5 min, se invalida al escribir y se
  precalienta al arrancar.
- Las integraciones se prueban antes de guardar y nunca se accede a las bases
  de datos de Radarr/Sonarr.
