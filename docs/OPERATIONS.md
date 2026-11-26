# Operación y mantenimiento

## Servicio

```bash
sudo systemctl status tod.service
sudo systemctl restart tod.service
sudo journalctl -u tod.service -f
```

En Docker:

```bash
docker compose ps
docker compose logs -f tod
docker compose restart tod
```

Comprobación rápida:

```bash
curl -s http://127.0.0.1:8773/healthz
curl -s http://127.0.0.1:8773/api/system | python3 -m json.tool
```

## Logs

TOD escribe con niveles estándar (`INFO` por defecto). Para investigar un
fallo, arranca con `TOD_LOG_LEVEL=DEBUG`. En systemd la salida ya va a
`journalctl -u tod.service`; en Docker, a `docker compose logs -f tod`.

Actualizar:

```bash
cd /ruta/a/TOD
git pull
sudo systemctl restart tod.service
```

## Tareas programadas

El scheduler interno ejecuta las tareas periódicas. Se gestiona en
**Configuración → Tareas programadas**.

| Tarea | Por defecto |
|---|---|
| `worker-cycle` | cada 5 min |
| `attention-check` | cada 5 min |
| `qbit-limits` | cada 1 min |
| `rss-sync` | cada 30 min |
| `bridge` | cada 30 min |
| `arr-mirror` | cada 30 min |
| `monitor` | diario 04:20 |
| `restore-strm` | diario 04:30 (reconcilia y poda) |
| `backfill` | domingo 03:00 |
| `metadata` | lunes 05:00, desactivado |

Ejecutar a mano:

```bash
tod qbit-sync
tod rss-sync --limit 200 --delay 0.3
tod monitor --limit 100 --delay 2
```

## Monitorización por defecto

Todo título que **no esté en Radarr/Sonarr** queda monitorizado al entrar en
TOD. Así se busca un release y TOD genera su `.strm` sin tener que pedirlo a
mano. Cuando existe el `.strm`, la monitorización se cierra sola.

`library_added` indica la pertenencia a la biblioteca de TOD, mientras que
`library_files` (y `has_file`) refleja los ficheros reales indexados desde
Radarr/Sonarr. Si un título tiene respaldo activo y pierde su fichero en
Radarr/Sonarr, TOD lo vuelve a monitorizar; si entra en Radarr/Sonarr con
fichero, deja de perseguirse desde TOD.

## Añadir y procedencia

**Añadir** es una sola caja. Acepta:

- un título suelto (`Matrix`, `Breaking Bad`), que se busca en TMDB;
- una URL de TMDB, una clave (`movie:tmdb:...`, `series:tmdb:...:S:E`), un ID;
- una lista de enlaces o IDs, una por línea;
- un `.torrent` manual, que se envía a qBittorrent. Es un atajo explícito:
  no pasa por admisión ni crea un compromiso de seed.

La casilla **Monitorizar** decide si los títulos nuevos se buscan y se añaden a
Jellyfin cuando haya fuente. Sin esa marca solo se crea la identidad.

Añadir no descarga ni ocupa disco. Cada lote queda en
`intake_batches`/`intake_items` con el resultado de cada línea, aunque falle.
`media_items.origin` guarda de dónde vino el título (`import`, `radarr`,
`sonarr`, `rss`, `tmdb`, `backfill`, `torrent`, `legacy`…). La primera
procedencia no se pisa al actualizar metadatos.

## Reproducción

`.strm` estable:

```text
http://127.0.0.1:8773/play?key=movie:tmdb:9737&download=0
```

- `download=0`: uso normal desde Jellyfin. Si el `.strm` está frío, un
  `GET` con `Range` (un reproductor real) autoriza el prepare; un escaneo
  sin `Range` sigue recibiendo 403.
- `download=1`: fuerza el arranque desde la app o un cliente explícito.
- `/status?key=...`: progreso, velocidad, seeds y estado de arranque:
  `stream_ready`, `playback_state` (`preparing`, `switching`, `buffering`,
  `ready`), `prepare_ms`, `first_byte_ms` y `switch_count`.
- `/sources?key=...`: versiones vivas y su salud.

Diagnóstico:

```bash
curl -s 'http://127.0.0.1:8773/status?key=movie%3Atmdb%3A9737' | python3 -m json.tool
curl -s -r 0-1048575 'http://127.0.0.1:8773/play?key=movie%3Atmdb%3A9737' -o /tmp/test.bin
```

## qBittorrent

- `qbit-sync` es solo lectura.
- La escritura (añadir, pausar, mover) está aislada en `qbit_write.py`.
- Límites por categoría: `radarr`, `tv-sonarr` y `ondemand`.
- El worker no mueve ni borra contenido: qBittorrent conserva la gestión de
  los ficheros.

```bash
tod qbit-version
tod qbit-torrents --category ondemand
tod qbit-sync --dry-run
tod qbit-limits
```

## Seed y espacio

- Privados: TOD mantiene el torrent en **force-start** hasta cumplir el
  mínimo de días y ratio. Al cumplirlo, lo suelta y **qBittorrent decide**
  si lo para o lo borra. Si estaba pausado, lo **reanuda** antes de forzar.
- El mínimo cuenta desde que **termina la descarga**, no desde que empieza:
  se usa `seed_started_at` de qBittorrent (`seeding_time`) y, si aún no hay
  dato, `completed_at`. Sin inicio de siembra conocido no se libera nada.
- TOD lee el mensaje del tracker en cada ciclo. Distingue
  `unregistered` (el tracker ya no conoce el torrent),
  `client_not_allowed` (cliente o versión fuera de la whitelist) y
  `not_authorized` (passkey o cuenta). Se guarda en el compromiso y se
  expone en `/status` (`seed.tracker_verdict`); si el cliente no está
  permitido, hay que cambiar de versión de qBittorrent o pedir el alta.
- Al mover a archivo, TOD verifica que qBittorrent sigue viendo los
  ficheros. Si el destino deja el torrent en `missingFiles`, revierte el
  movimiento en vez de dejar de sembrar en silencio.
- El borrado desde TOD está desactivado por defecto. Se puede activar en
  **Configuración → Trackers y Siembra**; entonces aparece el botón de borrar en
  **Cola**, con opción de quitar solo el torrent o borrar también ficheros.
- Al descubrir un tracker privado sin política, TOD crea una inicial con los
  valores de **Configuración → Trackers y Siembra** (por defecto 72 h y ratio 1,0;
  cada instalación puede cambiarlos).
- La privacidad sale del indexer (Prowlarr `privacy`, Jackett `<type>`), no se
  asume privada. Se puede corregir por tracker en la tabla de Siembra o por
  CLI: `tod set-policy DonTorrent --privacy public`. Al marcar público, TOD
  pone 0 días/ratio, actualiza sus releases y cierra las siembras abiertas.
  También se puede cambiar el mínimo:
  `tod set-policy Tracker --min-seed-hours 24 --target-ratio 0.5`.
- **Búsqueda de respaldo en los *arr**: activa por defecto. TOD busca
  títulos de Radarr/Sonarr que no tengan fichero ni descarga en cola, como
  si el *arr hubiera perdido sus ficheros. Se procesan por lotes (50 cada
  ciclo del worker y 200 en el trabajo `fallback-sweep`), con búsqueda por
  título local, original e ID. Los títulos siguen monitorizados hasta que
  el *arr recupere el fichero o los ponga en cola. **Monitorizar** en la ficha
  sigue siendo la vía para forzar uno concreto.
- **Retención hasta la biblioteca**: por defecto TOD no permite borrar una
  descarga completada hasta que `library_files` confirma que ya está en
  Radarr/Sonarr. Si el puente falla, espera como máximo
  `seed.keep_max_hours` (0 = sin límite) antes de permitir el borrado, para
  no perder la única copia. Se ajusta en **Configuración → Trackers y Siembra**.
- Públicos: sin compromiso de seed, pero la retención hasta la biblioteca
  sigue aplicando.
- `evict`, `reseed`, `abandon` y `clean-broken` ya no se ejecutan en el ciclo
  automático: el borrado pertenece a qBittorrent (`move-completed` sigue activo
  para mover descargas completadas al archivo sin dejar de sembrar).

```bash
tod policies
```

## Idioma

Se configura en **Configuración → Idioma de las fuentes**:

- **Cualquier idioma**: no filtra por audio.
- **Castellano primero**: si el título es originalmente español, manda el
  país de origen (`production_countries`/`origin_country` de TMDB). Un título
  español de España usa castellano; uno latinoamericano usa latino como V.O.
  En el resto de títulos prefiere castellano, luego V.O./VOSE, y descarta el
  doblaje latino.
- **Solo castellano**: solo acepta castellano de España.

Una coproducción con España se considera castellano. Si TMDB no da país, el
latino de un título originalmente español se acepta como V.O. pero después del
castellano. Los títulos sin idioma detectado solo entran como último respaldo.

Al actualizar una instalación anterior hay que rellenar el país una vez:
**Actividad → Tareas → Actualizar metadatos**, marcando «Revisar también los ya
rellenados». Sin ese paso, los títulos importados de Radarr/Sonarr no tienen
país y el latino se queda como respaldo.

## Almacenamiento

Valores por defecto en una instalación systemd:

```text
accesos .strm:    /mnt/catalogo
películas:        /mnt/library/Peliculas
series:           /mnt/library/Series
descargas:        /mnt/descargas
on-demand:        /mnt/descargas/ondemand
archivo:          /mnt/library/descargas
```

En Docker se usan `/downloads`, `/library` y `/media/catalogo` dentro del
contenedor, montados desde `./data/`.

**Configuración → General y Rutas** solo pide cuatro decisiones:

1. **Accesos para Jellyfin**: dónde escribe TOD los `.strm`.
2. **Biblioteca existente**: las carpetas de Películas (Radarr) y Series (Sonarr).
3. **Descargas**: la raíz de qBittorrent y la carpeta on-demand.
4. **Archivo de torrents**: dónde se guardan los torrents terminados. Vacío =
   la carpeta común de la biblioteca `/descargas`.

La carpeta común de la biblioteca, la lista de rutas `.strm` permitidas y la
raíz de semillas se calculan solas a partir de esas cuatro rutas. Solo hace
faltar fijarlas a mano en casos especiales, con `CATALOGO_LIBRARY_ROOT`,
`CATALOGO_STRM_ROOTS` y `CATALOGO_SEED_ROOT`.

TOD **no recorre las bibliotecas de Radarr/Sonarr**. `library_files` se
reconstruye desde sus APIs y `qbit-sync` mantiene el estado de los torrents;
las rutas de archivo se dan por buenas sin hacer `stat` por red, también cuando
la biblioteca está en un disco remoto (rclone, cloud, etc.). Solo se comprueba
el disco local de descargas y el fichero concreto al servir una reproducción.

Los torrents on-demand que terminan se mueven solos al **Archivo de torrents**
y siguen sembrando desde allí. La tarea **Mover completados a archivo**
(Configuración → Tareas) corre cada 10 minutos y mueve hasta 10 torrents por
pasada: ahí puedes cambiar la cadencia, el límite, la gracia, el destino o
desactivarla. Solo afecta a descargas on-demand de trackers privados;
Radarr/Sonarr no se mueven.

## Copias

La base y las claves están en `var/tod.db` (en Docker, `/config/tod.db`). Con
el servicio parado:

```bash
sqlite3 var/tod.db ".backup '/ruta/segura/tod-$(date +%F).db'"
```

No copies `*.db-wal` por separado. Para restaurar, para el servicio, sustituye
`var/tod.db` y arranca.

## Problemas frecuentes

| Síntoma | Comprobación |
|---|---|
| Panel sin conexión | `systemctl status tod.service`, `curl /healthz` |
| qBittorrent no aparece | URL, usuario y contraseña en Configuración |
| Prowlarr vacío | API key en Configuración; debe listar indexers |
| Jackett falla | API key y que Jackett tenga indexers configurados |
| Jellyfin 401 | API key de Jellyfin |
| Reproducción 403 | La petición no trae `Range` ni `download=1`; un reproductor real sí lo trae. Revisa también capacidad on-demand y política del tracker |
| Sin espacio | `df -h` y límite on-demand; qBittorrent decide qué se borra |
| Tarea atascada | `Tareas` en la web; `jobs` guarda estado y error |
