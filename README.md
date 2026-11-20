# TOD — Torrent on Demand

[![tests](https://github.com/GitCorion/TOD/actions/workflows/tests.yml/badge.svg)](https://github.com/GitCorion/TOD/actions/workflows/tests.yml)
[![license](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![python](https://img.shields.io/badge/python-3.11%2B-3776AB.svg)](https://www.python.org/)

**Catálogo bajo demanda para qBittorrent y Jellyfin.**

TOD unifica catálogo, reproducción y administración en un servicio. Se integra
con qBittorrent, Jellyfin, Prowlarr, Jackett y, opcionalmente, Radarr/Sonarr.
Esos servicios siguen siendo independientes: el `docker-compose.yml` incluido
los levanta juntos para que la instalación sea de un solo comando.

No hace falta editar bases de datos ni archivos de configuración de Prowlarr.
Las URLs y API keys se introducen en la web y TOD aplica el resto. En Docker,
`.env` solo aporta valores iniciales como UID/GID, IP permitida y claves.

## Qué puedes hacer

- **Buscar y añadir** películas y series desde TMDB.
- **Publicar `.strm` multiversión** para Jellyfin: un `.strm` principal y una
  variante por versión reproducible.
- **Reproducir bajo demanda** desde qBittorrent con rangos HTTP reales (206).
- **Sembrar con reglas** por tracker: tiempo mínimo, ratio, abandono y reseed.
- **Descubrir fuentes** en Prowlarr, Jackett o ambos, sin listas de trackers escritas a mano.
- **Leer Radarr/Sonarr por API**, nunca tocando sus bases de datos.
- **Automatizar** RSS, seguimiento, backfill, puente, restauración y límites de qBittorrent.
- **Configurar integraciones, directorios y tareas** desde
  **Sistema → Configuración**, con pruebas de conexión.

## Cómo funciona

```text
Jellyfin ──.strm──▶ TOD :8773 ──▶ fichero local / archivo
                         │
                         ├──▶ qBittorrent (descarga y seed)
                         ├──▶ Prowlarr    (indexers)
                         ├──▶ Jackett     (indexers)
                         └──▶ Radarr/Sonarr (solo API, biblioteca)
```

Un único proceso sirve:

- la web y su API (`/`, `/api/*`),
- el motor de reproducción (`/play`, `/resolve`, `/sources`, `/status`,
  `/catalog/lookup`, `/prepare`, `/lease`),
- el scheduler interno de tareas periódicas.

## Versiones y `.strm`

Cada película o episodio publicado tiene un `.strm` principal. Películas y
episodios van en rutas distintas:

```text
movies/Título (Año)/Título (Año).strm
series/Título (Año)/Season 01/Título S01E01.strm
```

Si hay **más de una versión reproducible**, se añade una variante por versión:

```text
movies/Título (Año)/Título (Año) - 1080p Cast.strm
movies/Título (Año)/Título (Año) - 4K VO - Tracker.strm
series/Título (Año)/Season 01/Título S01E01 - 1080p Cast.strm
```

- El principal resuelve la mejor fuente disponible al reproducir.
- Cada variante apunta a una versión concreta y aparece como versión distinta
  en Jellyfin, para elegir calidad o audio.
- El nombre usa calidad y audio; si se repiten, añade tracker o id.
- Por defecto solo se publican versiones reproducibles: fichero local, copia en
  archivo o debrid cacheado.
- Si el título solo está en Radarr/Sonarr, se publica el principal aunque el
  catálogo no tenga releases. En series, cada episodio recibe su `.strm`.

## Instalación con Docker Compose

Es la opción recomendada para una instalación completa: TOD, qBittorrent,
Jellyfin, Prowlarr y Jackett, cada uno en su contenedor y conectados entre sí.

```bash
git clone https://github.com/GitCorion/TOD.git
cd TOD
cp .env.docker.example .env
# edita .env: IP del servidor, UID/GID y claves
docker compose up -d --build
```

Si abres el panel por IP, añade esa IP a `TOD_WEB_ALLOWED_HOSTS` en `.env`. Por
defecto el compose solo permite `localhost` y la red interna.

Servicios y puertos:

| Servicio | URL | Para qué |
|---|---|---|
| TOD | `http://IP_DEL_SERVIDOR:8773` | catálogo, alta y configuración |
| Jellyfin | `http://IP_DEL_SERVIDOR:8096` | reproducir la biblioteca |
| qBittorrent | `http://IP_DEL_SERVIDOR:8080` | descargas y seed |
| Prowlarr | `http://IP_DEL_SERVIDOR:9696` | indexers |
| Jackett | `http://IP_DEL_SERVIDOR:9117` | indexers |

Radarr y Sonarr no están en el compose: si los usas, conéctalos por URL y API
key desde **Configuración → Integraciones**. Prowlarr y Jackett sí están, pero
no es necesario usar los dos: TOD consulta las fuentes que configures.

Primera puesta en marcha:

1. Abre TOD en `8773`.
2. Configura qBittorrent con `http://qbittorrent:8080` y sus credenciales.
3. Configura Prowlarr, Jackett o ambos, y pega la API key de Jellyfin.
4. En Jellyfin, añade `/media/catalogo/movies` como **Películas** y
   `/media/catalogo/series` como **Series**.
5. En TOD, revisa **Configuración → Directorios**.

Los detalles de volúmenes, claves y primer arranque están en
[docs/DOCKER.md](docs/DOCKER.md).

## Instalación con systemd

Para instalar sobre un servidor Linux ya existente, sin contenedores:

```bash
git clone https://github.com/GitCorion/TOD.git
cd TOD
sudo bash deploy/install-unified.sh
```

Requiere Python 3.11+. El instalador crea `/etc/tod.env` si no existe, instala
`tod.service`, desactiva servicios antiguos y arranca TOD en `0.0.0.0:8773`.

Después, abre `http://IP_DEL_SERVIDOR:8773` y configura:

1. **qBittorrent**: URL, usuario y contraseña.
2. **Prowlarr**: URL y API key (opcional si usas Jackett).
3. **Jackett**: URL y API key (opcional si usas Prowlarr).
4. **Jellyfin**: URL y API key.
5. **Directorios**: biblioteca del catálogo, descargas, semillas y biblioteca existente.
6. **Radarr/Sonarr** (opcional): URL y API key. TOD solo usa su API.

Prowlarr y Jackett son alternativas: configura uno o ambos.

Cada integración tiene **Probar** para validar antes de guardar; cada directorio
tiene **Comprobar**. Los secretos se guardan en `var/tod.db` con permisos
`0600` (en Docker, `/config/tod.db`) y nunca se devuelven al navegador.

## Integraciones

| Servicio | Uso en TOD | Necesario |
|---|---|---|
| qBittorrent | Descargar, sembrar y servir torrents | Sí, para reproducir bajo demanda |
| Prowlarr | Descubrir releases y comprobar salud | Uno de los dos |
| Jackett | Descubrir releases | Uno de los dos |
| Jellyfin | Reproducir los `.strm`; TOD le avisa de rutas nuevas | Recomendado |
| Radarr | Biblioteca y cola, solo API | Opcional |
| Sonarr | Biblioteca y cola, solo API | Opcional |
| TMDB | Metadatos, pósters y sinopsis | Recomendado |

TOD se integra con Radarr y Sonarr mediante sus APIs. Solo necesita la URL y
la API key de cada servicio.
Los trackers se descubren de la fuente que configures: Prowlarr (incluido el
idioma de cada indexer) o Jackett (`all`, con el indexer real de cada
resultado). Puedes usar uno o ambos; no hay listas privadas en el repositorio.

## Configuración desde la web

**Sistema → Configuración** incluye:

- **Integraciones** con prueba de conexión.
- **Directorios** con comprobación de existencia, escritura y espacio.
- **Reproducción**: descargas simultáneas, tamaño máximo y URL base de `.strm`.
- **Seguridad**: token web y hosts permitidos.
- **Tareas programadas**: activar, cambiar cadencia, ver la próxima ejecución y lanzar ahora.

Los valores se guardan en SQLite y mandan sobre `/etc/tod.env`. Los cambios se
aplican en caliente, salvo la URL base de reproducción, que requiere reinicio.
El puerto se fija al arrancar el servicio.

## Seguridad

- Pensado para LAN o WireGuard. No publiques el puerto en Internet.
- `/prepare` y `/lease` solo aceptan peticiones desde loopback.
- `download=1` en `/play` inicia una descarga explícita. Las rutas de
  reproducción no usan el token del panel: restringe el puerto a LAN/WireGuard.
- Los secretos viven en `var/tod.db` (`0600`, en Docker `/config/tod.db`);
  `.env` y `var/` están ignorados por git.
- Lee [SECURITY.md](SECURITY.md) antes de exponer el servicio.

## Desarrollo

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
python -m pytest -q
```

TOD se distribuye como paquete Python y se ejecuta con el comando `tod`:

```bash
tod --help
tod service --host 0.0.0.0 --port 8773
```

## Documentación

- [Docker Compose](docs/DOCKER.md)
- [Arquitectura](docs/ARCHITECTURE.md)
- [Operación y mantenimiento](docs/OPERATIONS.md)
- [Integraciones paso a paso](docs/INTEGRATIONS.md)
- [Cómo contribuir](CONTRIBUTING.md)
- [Seguridad](SECURITY.md)

## Licencia

MIT. Consulta [LICENSE](LICENSE).
