# Integraciones

TOD configura cada servicio desde **Sistema → Configuración**. Cada bloque
tiene un botón **Probar** que no guarda nada.

Prowlarr y Jackett son dos fuentes válidas de indexers. TOD puede consultar
ambas; Jackett no es un fallback público. La comprobación de salud, el RSS y la
búsqueda histórica usan la API de Prowlarr.

## qBittorrent

| Campo | Ejemplo |
|---|---|
| URL | `http://127.0.0.1:8080` (Docker: `http://qbittorrent:8080`) |
| Usuario | el de la Web UI (Docker: `admin`) |
| Contraseña | la de la Web UI del servicio |

TOD usa qBittorrent para descargar, sembrar y servir. El cliente de lectura no
expone operaciones de escritura; estas viven en `qbit_write.py` y solo se usan
desde admisión, workers y acciones explícitas.

## Prowlarr

| Campo | Ejemplo |
|---|---|
| URL | `http://127.0.0.1:9696/api/v1` |
| API key | Settings → General → API Key |

TOD toma todos los indexers habilitados, su idioma y su privacidad. Para
excluir un tracker, desactívalo en Prowlarr.

## Jackett

| Campo | Ejemplo |
|---|---|
| URL | `http://127.0.0.1:9117/api/v2.0` |
| API key | la de Jackett |

TOD consulta el indexer `all`. Cada resultado trae su indexer real y su tipo
(`public`/`private`). Puedes usar Jackett en lugar de Prowlarr o junto a él.

## Jellyfin

| Campo | Ejemplo |
|---|---|
| URL | `http://127.0.0.1:8096` (Docker: `http://jellyfin:8096`) |
| API key | Dashboard → API Keys |

TOD escribe los `.strm` en la biblioteca del catálogo y avisa a Jellyfin de
las rutas nuevas. La API key es opcional: sin ella no hay avisos de escaneo
ni detección de sesión, pero el play sigue funcionando porque un `GET` con
`Range` autoriza el prepare. Durante la reproducción, cada petición a `/play`
renueva el lease.

### Reproducción directa con fichero local

En **Configuración → Reproducción → Reproducción directa con fichero local**
(`strm_direct_local`, env `CATALOGO_STRM_DIRECT_LOCAL`) los `.strm` nuevos
contienen la **ruta absoluta del fichero** cuando ya existe en disco (o en el
mount de 1Fichier) y es legible, en vez de `http://127.0.0.1:8773/play?...`.
Así Jellyfin abre el vídeo directamente y TOD quita el proxy del medio.

- Las fuentes frías siguen escribiendo `/play?key=...&download=0`: la ruta
  directa solo se usa si el fichero está presente, también en las versiones
  (`&release=<id>`) que tengan su propio fichero.
- Si el fichero desaparece o el mount no está accesible al regenerar, el
  `.strm` vuelve a apuntar al proxy.
- `qbit-sync` reescribe los `.strm` de los títulos cuya ruta cambió, y
  `restore-strm` (04:30) reconcilia el resto. Hay que regenerar los `.strm`
  tras activar el ajuste.
- El inventario, los leases, `/catalog/lookup` y la limpieza reconstruyen la
  clave desde la ruta del fichero, así que siguen funcionando con `.strm`
  directos. Los leases se renuevan igual: evitan mover un pack mientras se
  está viendo.
- Tras cambiar la fuente preferida de un título con fichero local, hay que
  regenerar su `.strm` para que apunte al nuevo fichero.

### Arranque rápido de `.strm` (plugin Medusa)

Jellyfin sondea el `.strm` en cada reproducción. Con un torrent frío, el
ffprobe se atasca y el cliente espera al fichero completo. El plugin
`Jellyfin.Plugin.Catalogo` (Medusa) envuelve `IMediaSourceManager` y:

- pide `/media-info` a TOD (cabecera del fichero parcial: codecs, audio,
  duración y tamaño; si aún no hay fichero, metadata del release) y guarda
  los `MediaStream` del ítem;
- llama al manager interno con `allowMediaProbe=false`, así Jellyfin no
  lanza ffprobe y sirve el `.strm` en cuanto TOD tiene buffer;
- lanza `/prepare` para que TOD empiece la descarga.

`/media-info?key=...&release=...` responde:

```json
{
  "source": "ffprobe",
  "container": "matroska,webm",
  "duration_seconds": 3224.5,
  "size_bytes": 4664225386,
  "video": {"index": 0, "codec": "h264", "width": 1918, "height": 872},
  "audio": [{"index": 1, "codec": "eac3", "channels": 6, "language": "spa"}],
  "subtitles": [{"index": 3, "codec": "subrip", "language": "spa"}]
}
```

El plugin es de servidor: el cliente sigue hablando solo con Jellyfin. TOD
no se expone; el `.strm` apunta a `127.0.0.1` y Jellyfin lo proxya. Para
amigos fuera de la red se comparten cuentas de Jellyfin (con TLS o reverse
proxy si hace falta), no el puerto `8773`.

Al actualizar el plugin a mano, para Jellyfin antes de sustituir el DLL:
copiar sobre un proceso en marcha puede provocar `BadImageFormatException`.

## Radarr y Sonarr

| Campo | Ejemplo |
|---|---|
| URL Radarr | `http://127.0.0.1:7878/api/v3` |
| URL Sonarr | `http://127.0.0.1:8989/api/v3` |
| API key | Settings → General → API Key |

TOD se integra mediante la API: biblioteca, cola, historial y reescaneo. Solo
necesita la URL y la API key. Si no se usan, se pueden dejar vacíos.

## Trailarr

TOD expone un puente compatible con la API v3 de Radarr y Sonarr para que
Trailarr descargue los tráilers directamente en las carpetas `.strm` de TOD
(solo títulos con `library_added = 1` y carpeta existente en disco):

| Conexión en Trailarr | URL |
|---|---|
| Radarr | `http://<host-tod>:8773/trailarr/radarr` |
| Sonarr | `http://<host-tod>:8773/trailarr/sonarr` |

- **API Key**: usa el `Token de acceso web` de TOD si está configurado; si no hay
  token activo, sirve cualquier clave de 32 caracteres (p. ej.
  `0123456789abcdef0123456789abcdef`).
- **Ajuste en TOD (opcional)**: en **Sistema → Configuración → Integraciones →
  Trailarr** puedes configurar `URL` (`http://127.0.0.1:7889`) y `API key` para
  probar la conexión, supervisar su estado en Inicio o lanzar `trailarr-sync`.

## TMDB

Necesario para buscar títulos, pósters, sinopsis y colecciones. Se configura
con la API key de TMDB (v3).

## Rutas y directorios

| Ajuste | Uso |
|---|---|
| Accesos `.strm` (salida) | donde TOD escribe los `.strm` que lee Jellyfin |
| Películas en Radarr | raíz de Radarr (solo lectura) |
| Series en Sonarr | raíz de Sonarr (solo lectura) |
| Raíz de la biblioteca | opcional, si ambas comparten carpeta padre |
| Descargas | raíz de descargas en qBittorrent |
| Descargas on-demand | destino de descargas del catálogo bajo demanda |
| Archivo de torrents | destino al mover torrents completados (siguiendo sembrando) |

Usa **Comprobar** para ver existencia, escritura y espacio libre.
