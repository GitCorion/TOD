<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/tod-banner-dark.png">
    <img src="docs/assets/tod-banner-light.png" alt="TOD — Torrent on Demand. Catálogo bajo demanda para qBittorrent y Jellyfin." width="100%">
  </picture>
</p>

<p align="center">
  <a href="https://github.com/GitCorion/TOD/actions/workflows/tests.yml"><img alt="tests" src="https://github.com/GitCorion/TOD/actions/workflows/tests.yml/badge.svg"></a>
  <a href="LICENSE"><img alt="licencia MIT" src="https://img.shields.io/badge/license-MIT-blue.svg"></a>
  <a href="https://www.python.org/"><img alt="Python 3.11 o superior" src="https://img.shields.io/badge/python-3.11%2B-3776AB.svg"></a>
</p>

TOD unifica biblioteca, gestión de releases y reproducción bajo demanda en un
único servicio. Se integra con qBittorrent, Jellyfin, Prowlarr, Jackett y,
opcionalmente, Radarr/Sonarr. Todos siguen siendo independientes: el
`docker-compose.yml` incluido los levanta juntos en un solo comando.

<p align="center">
  <img src="docs/assets/catalog-dark.png" alt="Biblioteca de TOD: filtros por estado, perfil, calidad y descargas." width="100%">
</p>

<p align="center"><sub>TOD está en beta activa. La interfaz y la API pueden cambiar entre versiones menores.</sub></p>

No hace falta editar bases de datos ni archivos de configuración de Prowlarr.
Las URLs y API keys se introducen desde la web con validación previa; en Docker,
`.env` solo aporta valores iniciales como UID/GID, hosts permitidos y claves.

## Qué puedes hacer

- **Gestionar tu biblioteca** de películas, series y sagas desde TMDB con
  filtros avanzados, editor masivo, etiquetas y vistas en carteles o tabla.
- **Añadir desde una sola caja**: un título, un enlace o ID de TMDB, una lista
  completa o un `.torrent` manual, con historial de lotes importados.
- **Definir perfiles de calidad, Custom Formats y restricciones**: puntuación
  personalizada, calidad objetivo (*cutoff*), bloqueo de CAM/ISO y filtros de
  idioma (castellano primero, solo castellano o cualquier idioma).
- **Buscar y elegir releases**: **Búsqueda automática** y **Búsqueda
  interactiva** por título o temporada completa (con soporte de packs), fijar un
  release por defecto, forzar descargas o vetar releases en la **Blocklist**.
- **Controlar Buscados y Calendario**: vistas de *Faltantes*, *Mejorables*
  (debajo de la calidad objetivo) y *Próximos* estrenos, además de listas de
  importación automática desde TMDB o URLs JSON.
- **Crear entradas multiversión en Jellyfin**: un `.strm` principal y una
  variante por cada release reproducible, con plantillas de nombrado `.strm` y
  `.nfo` configurables.
- **Reproducir bajo demanda** desde qBittorrent mediante rangos HTTP reales
  (`206`) sin esperar a descargar el fichero completo.
- **Sembrar con reglas por tracker**: mínimo de días y ratio con `force-start`.
  Al cumplirse, TOD lo suelta y qBittorrent aplica sus reglas (el borrado desde
  TOD está desactivado por defecto).
- **Integrar Radarr y Sonarr por API** (solo lectura + espejo y respaldo),
  sin tocar nunca sus bases de datos.
- **Supervisar el sistema** desde **Inicio** y **Sistema → Estado**, con avisos
  directos al ajuste que los resuelve.

## Cómo funciona

```text
Jellyfin ──.strm──▶ TOD :8773 ──▶ fichero local / nube (archivo)
                         │
                         ├──▶ qBittorrent (cola, descarga y siembra)
                         ├──▶ Prowlarr    (indexers y salud)
                         ├──▶ Jackett     (indexers)
                         └──▶ Radarr/Sonarr (solo API, biblioteca)
```

Un único proceso sirve:

- la web y su API (`/`, `/api/*`),
- el motor de reproducción (`/play`, `/resolve`, `/sources`, `/status`,
  `/catalog/lookup`, `/prepare`, `/lease`),
- el scheduler interno de tareas programadas.

## El ecosistema Medusa

TOD es el motor de biblioteca y reproducción bajo demanda. Estas tres piezas se
complementan:

| Repositorio | Papel |
|---|---|
| [Medusa](https://github.com/GitCorion/Medusa) | Cliente nativo para Android TV. Reproduce desde Jellyfin. |
| [Medusa-Jellyfin-Plugin](https://github.com/GitCorion/Medusa-Jellyfin-Plugin) | Puente dentro de Jellyfin: expone `/Medusa/Status`, `/Prepare` y `/Lease`, y traduce un ítem `.strm` a una clave de TOD. |
| **TOD** (este repositorio) | Biblioteca, releases, cola de qBittorrent, `.strm` y streaming por rangos. |

El plugin es el nexo: Medusa no habla con TOD directamente; habla con Jellyfin
y el plugin traduce esa conversación. Sin el plugin, los `.strm` siguen
funcionando como cualquier otro ítem de Jellyfin, pero no hay progreso ni
preparación bajo demanda desde el cliente.

## Versiones y `.strm`

Cada película o episodio disponible en Jellyfin tiene un `.strm` principal:

```text
movies/Título (Año)/Título (Año).strm
series/Título (Año)/Season 01/Título S01E01.strm
```

Si hay **más de un release reproducible**, se añade una variante por cada uno
para elegir calidad o audio directamente en Jellyfin:

```text
movies/Título (Año)/Título (Año) - 1080p Cast.strm
movies/Título (Año)/Título (Año) - 4K VO - Tracker.strm
series/Título (Año)/Season 01/Título S01E01 - 1080p Cast.strm
```

- El `.strm` principal resuelve el mejor release disponible en el momento de
  reproducir.
- Cada variante apunta de forma estable al release concreto: si qBittorrent o
  Radarr/Sonarr mueven el fichero de ruta, la variante sigue funcionando sin
  regenerar el `.strm`.
- Solo se genera `.strm` si hay algo reproducible (**un release vivo con
  seeds** o un fichero local/en nube). Si una fuente muere, TOD intenta buscar
  otra al reproducir y retira el `.strm` en la reconciliación diaria si ya no
  queda ninguna alternativa.
- La biblioteca de TOD y los `.strm` de Jellyfin son dos niveles distintos: la
  biblioteca puede guardar miles de títulos monitorizados o en espera; Jellyfin
  solo ve los que ya tienen una fuente viva o un fichero disponible.

## Instalación

### Desde una release (Docker o Python)

Cada release publica una imagen en `ghcr.io/gitcorion/tod`, un wheel de Python y
un `docker-compose.release.yml` listo para usar:

- **Docker**: descarga `docker-compose.release.yml` y `env.docker.example` de la
  [última release](https://github.com/GitCorion/TOD/releases/latest), renombra
  el segundo a `.env`, ajusta la IP del servidor y las claves, y ejecuta
  `docker compose -f docker-compose.release.yml up -d`.
- **Python 3.11+**: instala el `.whl` de la release y ejecuta
  `tod service --host 0.0.0.0 --port 8773`.

### Con Docker Compose (desde código)

Levanta el stack completo (TOD, qBittorrent, Jellyfin, Prowlarr y Jackett):

```bash
git clone https://github.com/GitCorion/TOD.git
cd TOD
cp .env.docker.example .env
# edita .env: IP del servidor (TOD_WEB_ALLOWED_HOSTS), UID/GID y claves
docker compose up -d --build
```

| Servicio | URL | Para qué |
|---|---|---|
| TOD | `http://IP_DEL_SERVIDOR:8773` | biblioteca, releases y configuración |
| Jellyfin | `http://IP_DEL_SERVIDOR:8096` | reproducir la biblioteca |
| qBittorrent | `http://IP_DEL_SERVIDOR:8080` | cola, descargas y siembra |
| Prowlarr | `http://IP_DEL_SERVIDOR:9696` | indexers |
| Jackett | `http://IP_DEL_SERVIDOR:9117` | indexers (alternativa a Prowlarr) |

Los detalles de volúmenes, variables y primer arranque están en
[docs/DOCKER.md](docs/DOCKER.md).

### Con systemd (Linux sin contenedores)

```bash
git clone https://github.com/GitCorion/TOD.git
cd TOD
sudo bash deploy/install-unified.sh
```

Requiere Python 3.11+. Crea `/etc/tod.env` si no existe, instala `tod.service` y
arranca TOD en `0.0.0.0:8773`.

## Primera puesta en marcha

Al abrir TOD por primera vez (`:8773`), el asistente de **Primeros pasos** guía
y valida qBittorrent, fuentes (Prowlarr o Jackett), rutas y Jellyfin antes de
guardar. Si detecta Docker, propone automáticamente las URLs internas y las
rutas `/media/catalogo` y `/downloads`.

<p align="center">
  <img src="docs/assets/setup-dark.png" alt="Asistente de primeros pasos: qBittorrent, fuentes, directorios y Jellyfin, con validación antes de guardar." width="100%">
</p>

1. Valida **qBittorrent**, **Prowlarr o Jackett** (basta con uno) y las
   **rutas** desde el asistente.
2. En **Jellyfin**, añade `/media/catalogo/movies` como biblioteca de
   **Películas** y `/media/catalogo/series` como **Series**.
3. Si usas **Radarr** o **Sonarr**, conéctalos por URL y API key desde
   **Sistema → Configuración → Integraciones e Indexers**.

## Integraciones

| Servicio | Uso en TOD | Necesario |
|---|---|---|
| qBittorrent | Descargar, sembrar y servir torrents | Sí, para reproducir bajo demanda |
| Prowlarr | Descubrir releases y comprobar salud | Uno de los dos (Prowlarr o Jackett) |
| Jackett | Descubrir releases | Uno de los dos (Prowlarr o Jackett) |
| Jellyfin | Reproducir los `.strm` y avisar de rutas nuevas | Opcional |
| Radarr | Espejo de biblioteca y destino de lo descargado por TOD | Opcional |
| Sonarr | Espejo de biblioteca y destino de lo descargado por TOD | Opcional |
| TMDB | Metadatos, pósters, sinopsis y sagas | Recomendado |

Los trackers se descubren automáticamente desde Prowlarr o Jackett; no hay listas
privadas en el repositorio.

## Configuración desde la web

**Sistema → Estado** centraliza el diagnóstico (avisos, servicios, tareas,
discos y auditoría). **Sistema → Configuración** organiza todos los ajustes en
siete pestañas:

- **General y Rutas**: directorios (accesos `.strm`, bibliotecas de Radarr/Sonarr,
  descargas y archivo), límites de reproducción, idioma de releases y seguridad.
- **Integraciones e Indexers**: URLs y API keys con prueba de conexión, además
  de activación y estadísticas de indexers de Prowlarr.
- **Perfiles y Custom Formats**: perfiles de calidad, *cutoff*, reglas de
  puntuación (*Custom Formats*) y restricciones (*Must Contain / Must Not Contain*).
- **Gestión de medios (`.strm`)**: carpetas raíz, plantillas de nombrado para
  `.strm` y `.nfo`, separación de anime y vista previa de renombrado.
- **Etiquetas y Listas**: etiquetas de color y listas de importación automática
  (colecciones/populares/listas de TMDB o URLs JSON).
- **Trackers y Siembra**: días/ratio mínimos, `force-start`, retención hasta
  biblioteca y bloqueo o permisos de descarga por tracker.
- **Tareas programadas**: activar, ajustar cadencias, ver próximas ejecuciones
  y lanzar tareas manualmente.

Los cambios se guardan en SQLite (`var/tod.db`, o `/config/tod.db` en Docker) y
prevalecen sobre `.env`. La mayoría se aplican en caliente.

## Seguridad

- Pensado para LAN o WireGuard. **No publiques el puerto `8773` en Internet.**
- `/prepare` y `/lease` solo aceptan peticiones desde loopback.
- Las rutas de reproducción (`/play`, `/resolve`, `/sources`, `/status`) no
  usan el token del panel web: restringe el puerto a LAN/WireGuard.
- Los secretos viven en `var/tod.db` (`0600`); `.env` y `var/` están ignorados
  por git.
- Consulta [SECURITY.md](SECURITY.md) antes de exponer el servicio.

## Desarrollo

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
ruff check .
python -m pytest -q
```

También puede instalarse como paquete (`pip install .` y `tod service --host 0.0.0.0 --port 8773`),
en cuyo caso la base de datos se guarda en `~/.local/share/tod/tod.db`.

## Documentación

- [Docker Compose](docs/DOCKER.md)
- [Arquitectura](docs/ARCHITECTURE.md)
- [Diseño de la interfaz](DESIGN.md)
- [Operación y mantenimiento](docs/OPERATIONS.md)
- [Integraciones paso a paso](docs/INTEGRATIONS.md)
- [Glosario de la interfaz](docs/GLOSARIO.md)
- [Cómo contribuir](CONTRIBUTING.md)
- [Seguridad](SECURITY.md)

## Licencia

MIT. Consulta [LICENSE](LICENSE).
