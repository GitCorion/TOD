# Docker Compose

El compose levanta cinco contenedores:

| Contenedor | Imagen | Función |
|---|---|---|
| `tod` | build local | catálogo, web, reproducción y scheduler |
| `qbittorrent` | LinuxServer | descargas y seed |
| `jellyfin` | LinuxServer | biblioteca y reproducción |
| `prowlarr` | LinuxServer | indexers privados |
| `jackett` | LinuxServer | fallback público |

No incluye Radarr/Sonarr. Se pueden conectar por URL y API key si se usan.

## Arranque

```bash
cp .env.docker.example .env
docker compose up -d --build
```

Edita `.env` antes de arrancar:

- `PUID` y `PGID`: propietario de los ficheros en el host.
- `TZ`.
- `TOD_WEB_ALLOWED_HOSTS`: IP LAN/WireGuard. Sin ella, el panel rechaza la IP.
  La red interna (`tod`) se añade automáticamente.
- `QBIT_USER` y, si ya la tienes, `QBIT_PASS`.
- `PROWLARR_API_KEY`, `JACKETT_API_KEY`, `JELLYFIN_API_KEY`, `TMDB_API_KEY`.

## Primera configuración

1. **qBittorrent** (`:8080`): usuario `admin`; la contraseña temporal aparece en
   `docker compose logs qbittorrent`. Cámbiala.
2. **Prowlarr** (`:9696`): añade indexers y copia su API key.
3. **Jackett** (`:9117`): añade indexers y copia su API key.
4. **Jellyfin** (`:8096`): completa el asistente y crea una API key.
5. **TOD** (`:8773`): en Configuración, conecta los servicios y prueba cada uno.
6. **Jellyfin**: añade `/media/catalogo/movies` como biblioteca de películas
   y `/media/catalogo/series` como biblioteca de series.

TOD ya conoce las URLs internas (`http://qbittorrent:8080`,
`http://jellyfin:8096`, etc.) mediante variables de entorno. El compose añade
además `tod` a los hosts permitidos, para que Jellyfin pueda resolver y llamar
a `http://tod:8773`. Las claves se pueden poner en `.env` o en la web.

## Directorios

| Host | Contenedor | Uso |
|---|---|---|
| `./data/tod` | `/config` | base de datos y ajustes |
| `./data/media` | `/media` | `.strm` que lee Jellyfin |
| `./data/downloads` | `/downloads` | descargas y seed |
| `./data/library` | `/library` | biblioteca externa de Radarr/Sonarr |
| `./data/qbittorrent` | `/config` | configuración de qBittorrent |
| `./data/jellyfin` | `/config` | configuración de Jellyfin |
| `./data/prowlarr` | `/config` | configuración de Prowlarr |
| `./data/jackett` | `/config` | configuración de Jackett |

TOD escribe los `.strm` bajo `/media/catalogo/movies` y
`/media/catalogo/series`; Jellyfin ve las mismas rutas. Los `.strm` nuevos
apuntan a `http://tod:8773`, que Jellyfin resuelve dentro de la red de Compose.

## Copias

```bash
docker compose stop tod
cp data/tod/tod.db /ruta/segura/tod-$(date +%F).db
docker compose start tod
```

## Actualizar

```bash
git pull
docker compose up -d --build
```

## Si algo no conecta

```bash
docker compose ps
docker compose logs -f tod
```

- qBittorrent: usuario/contraseña y `http://qbittorrent:8080`.
- Prowlarr: API key y `http://prowlarr:9696/api/v1`.
- Jackett: API key y `http://jackett:9117/api/v2.0`.
- Jellyfin: API key y `http://jellyfin:8096`.

La web de TOD no debe publicarse directamente en Internet. Usa WireGuard o un
reverse proxy con TLS.
