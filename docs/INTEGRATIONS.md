# Integraciones

TOD configura cada servicio desde **Sistema → Configuración**. Cada bloque
tiene un botón **Probar** que no guarda nada.

Prowlarr y Jackett son dos fuentes válidas de indexers. TOD puede consultar
ambas; Jackett no es un fallback público. La comprobación de salud, el RSS y el
backfill usan la API de Prowlarr.

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

## Radarr y Sonarr

| Campo | Ejemplo |
|---|---|
| URL Radarr | `http://127.0.0.1:7878/api/v3` |
| URL Sonarr | `http://127.0.0.1:8989/api/v3` |
| API key | Settings → General → API Key |

TOD se integra mediante la API: biblioteca, cola, historial y reescaneo. Solo
necesita la URL y la API key. Si no se usan, se pueden dejar vacíos.

## TMDB

Necesario para buscar títulos, pósters, sinopsis y colecciones. Se configura
con la API key de TMDB (v3).

## Directorios

| Ajuste | Uso |
|---|---|
| Carpeta de los `.strm` | donde TOD escribe los `.strm` que lee Jellyfin |
| Biblioteca de películas | raíz de Radarr (solo lectura) |
| Biblioteca de series | raíz de Sonarr (solo lectura) |
| Raíz común | opcional, si ambas comparten carpeta padre |
| Descargas de qBittorrent | raíz de descargas |
| Carpeta on-demand | destino de descargas del catálogo |
| Raíces `.strm` | rutas válidas para `/catalog/lookup` |

Usa **Comprobar** para ver existencia, escritura y espacio libre.

## Real-Debrid (avanzado)

No es necesario para el uso normal y no aparece en la configuración. Solo se
activa con `RD_API_KEY` en el entorno, para releases públicos cacheados.
