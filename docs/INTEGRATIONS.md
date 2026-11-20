# Integraciones

TOD configura cada servicio desde **Sistema → Configuración**. Cada bloque
tiene un botón **Probar** que no guarda nada.

## qBittorrent

| Campo | Ejemplo |
|---|---|
| URL | `http://127.0.0.1:1340` |
| Usuario | `qbittorrent` |
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
(`public`/`private`); el fallback público descarta los privados.

## Jellyfin

| Campo | Ejemplo |
|---|---|
| URL | `http://127.0.0.1:8086` |
| API key | Dashboard → API Keys |

Jellyfin consume los `.strm` de la biblioteca del catálogo. TOD renueva leases
cuando Jellyfin informa de una reproducción activa.

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
| Biblioteca del catálogo | donde TOD escribe los `.strm` |
| Biblioteca existente | raíz de Radarr/Sonarr (solo lectura) |
| Descargas de qBittorrent | raíz de descargas |
| Carpeta on-demand | destino de descargas del catálogo |
| Raíces `.strm` | rutas válidas para `/catalog/lookup` |

Usa **Comprobar** para ver existencia, escritura y espacio libre.

## Real-Debrid (avanzado)

No es necesario para el uso normal y no aparece en la configuración. Solo se
activa con `RD_API_KEY` en el entorno, para releases públicos cacheados.
