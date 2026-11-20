# TOD — Torrent on Demand

[![tests](https://github.com/GitCorion/TOD/actions/workflows/tests.yml/badge.svg)](https://github.com/GitCorion/TOD/actions/workflows/tests.yml)
[![license](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![python](https://img.shields.io/badge/python-3.11%2B-3776AB.svg)](https://www.python.org/)

**Tu catálogo, tu qBittorrent y tu Jellyfin, en un solo servicio.**

TOD convierte tu servidor en un catálogo bajo demanda: busca títulos, los
publica como `.strm` para Jellyfin y descarga solo cuando alguien los
reproduce. Web, API, reproducción y tareas programadas viven en el mismo
proceso y se configuran desde el navegador.

No necesitas editar bases de datos, ni `config.xml`, ni archivos de entorno
para empezar. Pones las URLs y las API keys de tus servicios en la web y TOD
hace el resto.

## Qué puedes hacer

- **Buscar y añadir** películas y series desde TMDB.
- **Publicar `.strm`** en tu biblioteca de Jellyfin, con una versión por fuente.
- **Reproducir bajo demanda** desde qBittorrent con rangos HTTP reales (206).
- **Sembrar con reglas** por tracker: tiempo mínimo, ratio, abandono y reseed.
- **Descubrir fuentes** en Prowlarr y Jackett, sin listas de trackers escritas a mano.
- **Leer Radarr/Sonarr por API**, nunca tocando sus bases de datos.
- **Automatizar** RSS, seguimiento, backfill, puente, restauración y límites de qBittorrent.
- **Configurarlo todo** desde **Sistema → Configuración**, con pruebas de conexión.

## Cómo funciona

```text
Jellyfin ──.strm──▶ TOD :8773 ──▶ fichero local / archivo
                         │
                         ├──▶ qBittorrent (descarga y seed)
                         ├──▶ Prowlarr    (indexers privados)
                         ├──▶ Jackett     (fallback público)
                         └──▶ Radarr/Sonarr (solo API, biblioteca)
```

Un único proceso sirve:

- la web y su API (`/`, `/api/*`),
- el motor de reproducción (`/play`, `/resolve`, `/sources`, `/status`,
  `/catalog/lookup`, `/prepare`, `/lease`),
- el scheduler interno que sustituye a los timers.

## Instalación rápida

Requisitos: Linux, Python 3.11+ y, para el uso completo, qBittorrent,
Prowlarr, Jackett y Jellyfin. No hay dependencias Python externas.

```bash
git clone https://github.com/GitCorion/TOD.git
cd TOD
sudo bash deploy/install-unified.sh
```

El instalador crea `/etc/catalogo.env` si no existe, instala `tod.service`,
desactiva los servicios antiguos y arranca TOD en `0.0.0.0:8773`.

Abre `http://IP_DEL_SERVIDOR:8773` y completa la configuración:

1. **qBittorrent**: URL, usuario y contraseña.
2. **Prowlarr**: URL y API key.
3. **Jackett**: URL y API key.
4. **Jellyfin**: URL y API key.
5. **Directorios**: biblioteca del catálogo, descargas, semillas y biblioteca existente.
6. **Radarr/Sonarr** (opcional): URL y API key. TOD solo usa su API.

Cada bloque tiene **Probar** para validar antes de guardar. Los secretos se
guardan en `var/catalogo.db` con permisos `0600` y nunca se devuelven al
navegador.

## Integraciones

| Servicio | Uso en TOD | Necesario |
|---|---|---|
| qBittorrent | Descargar, sembrar y servir torrents | Sí, para reproducir bajo demanda |
| Prowlarr | Descubrir releases y comprobar salud | Recomendado |
| Jackett | Fallback público | Opcional |
| Jellyfin | Consumir los `.strm` y renovar leases | Recomendado |
| Radarr | Biblioteca y cola, solo API | Opcional |
| Sonarr | Biblioteca y cola, solo API | Opcional |
| TMDB | Metadatos, pósters y sinopsis | Recomendado |

TOD no lee ni escribe las bases de datos de Radarr/Sonarr. Usa sus API keys.
Los trackers se descubren de Prowlarr (incluido su idioma) y de Jackett
(`all`, con el indexer real de cada resultado): no hay listas privadas en el
repositorio.

## Configuración desde la web

**Sistema → Configuración** incluye:

- **Integraciones** con prueba de conexión.
- **Directorios** con comprobación de existencia, escritura y espacio.
- **Reproducción**: descargas simultáneas, tamaño máximo y URL base de `.strm`.
- **Seguridad**: token web y hosts permitidos.
- **Tareas programadas**: activar, cambiar cadencia, ver la próxima ejecución y lanzar ahora.

Los valores se guardan en SQLite y mandan sobre `/etc/catalogo.env`. Los
cambios se aplican en caliente, salvo el puerto y la URL base de reproducción.

## Seguridad

- Pensado para LAN o WireGuard. No publiques el puerto en Internet.
- `/prepare`, `/lease` y `/play?...&download=1` están restringidos a loopback.
- Define `CATALOGO_WEB_TOKEN` si el panel queda accesible desde la LAN.
- Los secretos viven en `var/catalogo.db` (`0600`); `.env` y `var/` están
  ignorados por git.
- Lee [SECURITY.md](SECURITY.md) antes de exponer el servicio.

## Desarrollo

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
python -m pytest -q
```

El paquete interno se llama `catalogo` por compatibilidad; el producto, el CLI
y el servicio se llaman TOD. El comando instalado es `tod`:

```bash
tod --help
tod service --host 0.0.0.0 --port 8773
```

## Documentación

- [Arquitectura](docs/ARCHITECTURE.md)
- [Operación y mantenimiento](docs/OPERATIONS.md)
- [Integraciones paso a paso](docs/INTEGRATIONS.md)
- [Cómo contribuir](CONTRIBUTING.md)
- [Seguridad](SECURITY.md)

## Licencia

MIT. Consulta [LICENSE](LICENSE).
