# Arquitectura

TOD es un único proceso Python con cuatro responsabilidades: web, API,
reproducción y scheduler. Comparten configuración, base de datos y clientes.

## Componentes

```text
catalogo/
├── web.py            SPA + API + rutas de reproducción
├── http.py           /play, /resolve, /sources, /status, /prepare, /lease
├── play_service.py   admisión, prepare, preflight y streaming
├── resolver.py       local_seed → library → torrent → unavailable
├── backend.py        qBittorrent de escritura, admisión y capacidad
├── scheduler.py      tareas periódicas internas
├── app_settings.py   ajustes tipados, secretos y recarga
├── integrations.py   pruebas de conexión
├── arr.py            clientes Radarr/Sonarr (solo API)
├── qbit_sync.py      inventario de qBittorrent en solo lectura
├── strm.py           generación de .strm multi-versión
└── web_static/       SPA sin dependencias
```

## Flujo de una reproducción

1. Jellyfin lee un `.strm` y llama a `/play?key=...&download=0`.
2. TOD resuelve la clave: fichero local, biblioteca, debrid o torrent.
3. Si el torrent ya está preparado, sirve rangos con `206`.
4. Si está frío, `/play` responde `403` salvo `download=1` explícito.
5. La app puede llamar a `/prepare` (solo loopback) para iniciar la descarga.
6. El scheduler sincroniza qBittorrent, mueve completados y mantiene la biblioteca.

## Descubrimiento de trackers

- **Prowlarr**: TOD usa todos los indexers habilitados. De cada uno lee
  `name`, `language` y `privacy`; el idioma del indexer se usa para inferir el
  audio cuando el título no trae una etiqueta explícita.
- **Jackett**: consulta el indexer `all`; cada resultado incluye
  `<jackettindexer>` y `<type>`, así que TOD conoce el tracker real y descarta
  los privados en el fallback público.
- No hay listas de trackers en el código ni en la configuración.

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

SQLite en `var/tod.db` (`0600`, WAL). Tablas principales:

| Tabla | Contenido |
|---|---|
| `media_items` | películas y episodios con clave estable |
| `releases` | candidatos de Prowlarr/Jackett |
| `torrents`, `torrent_files` | estado real de qBittorrent |
| `library_files` | ficheros de Radarr/Sonarr vía API |
| `seed_commitments` | tiempo y ratio de seed |
| `play_intents`, `stream_leases` | reproducción y leases |
| `tracker_policies` | reglas por tracker |
| `jobs` | cola persistente y scheduler |
| `settings` | configuración web y secretos |
| `events` | auditoría |

## API principal

| Ruta | Uso |
|---|---|
| `/api/overview`, `/api/activity` | panel |
| `/api/ondemand/*` | catálogo, añadir, descargar |
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
- El scheduler usa `jobs`, que serializa tareas y evita solapamientos.
- Las integraciones se prueban antes de guardar y nunca se accede a las bases
  de datos de Radarr/Sonarr.
