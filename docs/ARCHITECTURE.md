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
├── api_ondemand.py   biblioteca, ajustes y descargas bajo demanda
├── api_titles.py     alta, ficha, biblioteca y monitorización
├── api_intake.py     recepción, listas y torrents manuales
├── api_tmdb.py       búsqueda, temporadas y episodios
├── api_releases.py   fijar, verificar y preparar releases
├── api_monitor.py    buscados, indexers y siembra
├── api_preferences.py idioma y reglas de siembra
├── api_settings.py   configuración, tareas, avisos y sistema
├── api_dashboard.py  portada, actividad, calendario y búsqueda
├── api_downloads.py  descargas de qBittorrent
├── api_jobs.py       tareas: lista, detalle y cancelación
├── api_library.py    sagas y series
├── errors.py         WebError común a la API
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
2. TOD resuelve la clave: fichero local, biblioteca o torrent.
3. Si no hay fuente viva, `/play` busca en Prowlarr/Jackett y guarda los
   candidatos.
4. Si el torrent ya está preparado, sirve rangos con `206`.
5. Si está frío, un `GET` con `Range` autoriza el prepare; un escaneo sin
   `Range` sigue recibiendo `403`.
6. Elige primero lo que ya se puede servir (local/biblioteca), después los
   torrents con swarms probados. Si la primera fuente no llega a buffer en
   su ventana, cambia a la siguiente dentro de la misma petición.
7. Un seek lejano en frío no bloquea: responde `503 seek_not_ready` con
   `X-Catalogo-Seek: pending` y `Retry-After`, porque qBittorrent descarga en
   orden y no permite priorizar una pieza suelta.
8. El scheduler sincroniza qBittorrent, espeja Radarr/Sonarr, busca fuentes,
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
| `/api/ondemand/recent` | últimas incorporaciones disponibles |
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

## Motor torrent

TOD no implementa BitTorrent: **orquesta qBittorrent**. Un motor propio
anunciaría con un cliente que los trackers privados no tienen en su
whitelist, así que no se contempla: el announce y la siembra pertenecen a
qBittorrent. TOD mide el buffer, cambia de fuente, fuerza o reanuda la
siembra y vigila el veredicto del tracker, pero no sustituye al cliente.

## Radarr, Sonarr y el respaldo

Radarr/Sonarr son la biblioteca a largo plazo; TOD es la capa de
reproducción y el respaldo del catálogo. La propiedad depende del estado:

| Estado en el *arr | TOD |
|---|---|
| Tiene el fichero | Indexa `library_files`, sirve desde la biblioteca del *arr y poda su `.strm`. |
| Tiene una descarga en cola | Genera el `.strm`, adopta ese torrent y lo fuerza al reproducir. No crea otro ni un compromiso de siembra. |
| No tiene fichero ni cola | `arr_fallback`: lo monitoriza y busca fuentes por lotes; si el *arr lo pone en cola o recupera el fichero, deja de perseguirlo. |
| No está en el *arr | Lo posee TOD: búsqueda, descarga, `.strm` y siembra. |

El puente nunca borra una descarga del cliente (`remove_from_client=False`):
solo retira el registro de la cola del *arr, que cierra su importación por
rescan. El orden de búsqueda es **ID externo primero** (TMDB/TVDB/IMDb,
con temporada/episodio en series), después título traducido/original y por
último Jackett. Así no se confunden remakes ni versiones de otro año.

## Packs de temporada

En un pack, `prepare_download` solo enlaza el capítulo que disparó la
descarga (`torrent_files.asset_id`). El resto nace sin ficha a propósito: los
ficheros se van completando al descargarse. Cuando el pack lo descargó
Sonarr, su historial aporta la lista de episodios; cuando lo descargó TOD no
hay historial, así que `qbit_sync.relink_pack_files` empareja cada fichero
`SxxEyy` con las fichas locales de la misma serie (TMDB/TVDB) y rellena su
`asset_id` sin volver a pedir la lista a qBittorrent.

Con el pack mapeado, el puente publica todos sus episodios en la misma
pasada y Sonarr los ve tras un único reescaneo. El `limit` del puente cuenta
descargas, no ficheros.
