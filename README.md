# TOD — Torrent on Demand

TOD es el producto: un **servicio único** que reúne el catálogo, el motor de
reproducción bajo demanda, el panel web y el scheduler. La configuración de
integraciones y directorios se hace desde la propia web.

El paquete interno sigue llamándose `catalogo` y la base `var/catalogo.db` por
compatibilidad con las migraciones y los despliegues existentes.

La `assets.db` de la raíz es la referencia legacy: **se lee, no se escribe**.

La aplicación ya puede añadir torrents bajo demanda, pero solo a través de la
puerta de admisión (`seed_commitment`) y con los workers en dry-run por
defecto. Prowlarr se usa para salud e ingesta privada; Jackett para el fallback
público. Real-Debrid queda como opción avanzada por entorno y no es necesario
para el uso normal.

Los ficheros de la versión anterior (`assets.db`, `build_assets.py`,
`health_prowlarr.py`, `resolver.py`, `MEMORIA.md`) se conservan en la raíz,
ignorados por git. La versión nueva es el paquete `catalogo/`.

## Qué está construido

- **Esquema nuevo** (`migrations/001` a `014`) con:
  - `media_items`: clave estable (`movie:tmdb:...`, `series:tmdb:...:s:e`),
    `media_id`, `tvdb_id`, estado (`COLD`, `DOWNLOADING_LOCAL`,
    `SEEDING_LOCAL`, `ON_1FICHIER`...) y `active_file_id`.
  - `releases`: candidatos con tracker, calidad, audio, salud e `info_hash`.
  - `torrents`: estado real de qBittorrent, categoría, progreso, ratio,
    `base_path` y fechas.
  - `torrent_files`: ficheros/packs con `file_index`, `rel_path`,
    `storage_location` (`LOCAL_DISK`/`1FICHIER`) y `cloud_uploaded_at`.
  - `seed_commitments`: compromiso de admisión/seed.
  - `play_intents`: intención de reproducción separada del seed.
  - `play_targets`: puntero mutable heredado.
  - `tracker_policies`: normas de seed por tracker.
- **Importador no destructivo** de la `assets.db` actual.
- **Resolución por clave**: `local_seed -> library -> torrent -> unavailable`.
- **HTTP local correcto**: 200/206/304/416, `HEAD`, `ETag`, `Last-Modified`,
  `Range` con sufijo (`bytes=-1000`) y sin el bug de longitud negativa.
- **Backend de torrents bajo admisión**: si el puntero es un torrent, `/play`
  solo arranca la descarga con `&download=1`, después de crear `play_intent` y
  `seed_commitment`. Sin ese flag, `/play` es solo lectura.
- **`.strm` multi-versión** como `/mnt/palantir`: un `.strm` principal y un
  `.strm` por versión (`Title - 1080p Cast.strm`) con `&release=<id>`, para
  que Jellyfin/app pueda elegir y saltar de fuente. Los `.strm` llevan
  `&download=0`: Jellyfin nunca arranca descargas.
- **Admisión de descargas** con compromiso de seed, espacio reservado y
  límite por tracker. Sin intent aprobado, no se debe descargar.
- **Retención**: no se cierra/libera un intent hasta cumplir tiempo y ratio.
- **Salud manual**: clasificación `alive/stale/dead` y actualización de peers.
- **3A.1**: `play_intents`, cliente de escritura qBittorrent,
  `prepare_download` con `seed_commitment` obligatorio, prioridad de pack y
  `forceStart`. Probado con un release privado real de principio a fin.
- **Sync de qBittorrent en solo lectura**: indexa las 836 torrents de todas
  las categorías, mapea Radarr/Sonarr por `DownloadId` y rellena
  `torrent_files`/`media_items` sin escribir en qBittorrent.
- **Límites por categoría**: Radarr/Sonarr limitados por torrent y `ondemand`
  ilimitado cuando el global de qBittorrent está a 0. Timer systemd cada
  minuto.
- **Selección por calidad y tamaño**: primero la mejor calidad disponible y,
  dentro de la misma calidad, el fichero más pequeño. No hay límite duro de
  tamaño por defecto.
- **Abandono de descargas**: si un torrent bajo demanda no llega al 5 % y
  nadie lo está reproduciendo, se pausa o se borra según la política del
  tracker. En privados solo se pausa salvo autorización explícita.
- **Gateway HTTP** con `/sources`, `/play` y `/resolve`, incluida la
  reproducción de versiones ya archivadas en 1fichier.

## Instalación en tu servidor

Requisitos: Python 3.11+ y, para el uso completo, qBittorrent, Prowlarr,
Jackett y Jellyfin. No hay dependencias Python externas.

```bash
git clone https://github.com/GitCorion/TOD.git
cd TOD
sudo bash deploy/install-unified.sh
```

Después abre `http://IP_DEL_SERVIDOR:8773` y configura desde
**Sistema → Configuración**:

1. Prowlarr: URL y **API key** (no hace falta el `config.xml`).
2. Jackett: URL y API key.
3. qBittorrent: URL, usuario y contraseña.
4. Jellyfin: URL y API key.
5. Directorios de tu servidor y rutas de Radarr/Sonarr si los usas.

No hay claves ni rutas privadas en el repositorio: cada usuario pone las suyas
en la web. `var/`, `.env` y las bases de datos están ignorados por git.

### Publicar el repositorio

```bash
git remote add origin git@github.com:GitCorion/TOD.git
git push -u origin main
```

El workflow de `.github/workflows/tests.yml` ejecuta la suite en cada push.

## Servicio único TOD

Un solo proceso y una sola unidad systemd:

```
tod.service  ->  python3 -m catalogo.cli service --host 0.0.0.0 --port 8773
```

- La web (`/`, `/static`, `/api/*`) y la reproducción (`/play`, `/resolve`,
  `/sources`, `/status`, `/catalog/lookup`, `/prepare`, `/lease`) comparten
  servidor, base de datos y una única instancia de `PlayService`.
- El scheduler interno sustituye a los timers: worker, RSS, seguimiento,
  backfill, puente, restauración, límites de qBittorrent y metadatos.
- Los `.strm` existentes siguen apuntando a `http://127.0.0.1:8773`.
- `/prepare`, `/lease` y `/play?...&download=1` siguen restringidos a loopback.

Instalación:

```bash
sudo bash deploy/install-unified.sh
```

El instalador desactiva `catalogo-proxy`, `catalogo-web` y los timers antiguos,
e instala y arranca `tod.service`.

### Configuración desde la web

En **Sistema → Configuración**:

- **Integraciones**: Prowlarr, Jackett, qBittorrent, Jellyfin, Radarr/Sonarr,
  TMDB, con botón **Probar** sin guardar. Real-Debrid no aparece en la
  configuración normal: es una opción avanzada por entorno.
- **Directorios**: biblioteca del catálogo, biblioteca existente, descargas,
  on-demand y raíces `.strm`, con comprobación de existencia/escritura/espacio.
- **Reproducción**: límite de descargas on-demand, tamaño máximo de stream y
  URL base de los `.strm`.
- **Seguridad**: token web y hosts permitidos.
- **Tareas programadas**: activar/desactivar, cadencia, próxima ejecución y
  **Ejecutar ahora**.

Los valores se guardan en la tabla `settings` y mandan sobre el entorno. Los
secretos nunca se devuelven al navegador: solo se indica si están configurados.
Los cambios se aplican en caliente; solo la URL base de reproducción y los
cambios de puerto requieren reiniciar.

## Qué NO está activo

- No se generan `.strm` automáticamente; se exportan con `export-strm`.
- No se importa a Radarr/Sonarr.
- El worker mueve a 1fichier de forma automática; el abandono y la
  normalización de siembra siguen en dry-run salvo que se ejecuten a mano.
- El backend público con Real-Debrid es opcional y avanzado; sin `RD_API_KEY`
  se usa directamente el resto de fuentes.

## Tests

```bash
cd /opt/catalogo
python3 -m pytest -q
```

## Importar una copia de la base actual (sin tocar la original)

La base original es de root. Se recomienda copiarla y trabajar sobre la copia:

```bash
sudo cp /opt/catalogo/assets.db /opt/catalogo/var/legacy_assets.db
sudo chown "$USER:$USER" /opt/catalogo/var/legacy_assets.db
cd /opt/catalogo
python3 -m catalogo.cli import-legacy \
  --source var/legacy_assets.db \
  --dest var/catalogo.db
python3 -m catalogo.cli stats
```

La base nueva se crea en `var/catalogo.db` con permisos `0600`.

## Resolver y cambiar el puntero

```bash
python3 -m catalogo.cli resolve 'movie:tmdb:9737'
python3 -m catalogo.cli set-target 'movie:tmdb:9737' --source local_seed --file-id 2
python3 -m catalogo.cli targets --limit 20
```

`set-target` cambia solo la fila de `play_targets`. La clave y el `.strm`
siguen siendo los mismos.

## Previsualizar .strm (sin tocar Jellyfin)

```bash
python3 -m catalogo.cli export-strm \
  --out var/strm-preview \
  --base-url http://127.0.0.1:8773 \
  --limit 20
```

Para exigir salud `alive` y al menos 1 peer:

```bash
python3 -m catalogo.cli export-strm \
  --out var/strm-preview-health \
  --require-health --min-seeders 1
```

Por defecto (`--versions available`) solo se crean `.strm` de versión para
releases que ya se pueden reproducir: fichero en local/1fichier o release
cacheado en Real-Debrid. El `.strm` principal del título se sigue generando
para poder preparar releases en frío desde la app. Así Jellyfin no sondea
versiones que devolverían 403.

La poda de `.strm` (`--prune`) es **excepcional**, solo para restos de
pruebas. El flujo normal no borra `.strm`: si un título está en Radarr/Sonarr
se mantiene el puntero y el proxy sirve el fichero de la biblioteca oficial.
Si alguna copia desaparece, `restore-strm` vuelve a crear el `.strm`.

Como el worker de salud no es automático, solo se publican los releases que
ya tengan `health=alive` (o los que tengan fichero local). El resto sigue en
`unknown` y no sale con `--require-health`.

## Trackers desde Prowlarr y Jackett

TOD no guarda listas de trackers en el código.

- **Prowlarr**: usa todos los indexers habilitados. De cada uno toma el nombre,
  el idioma (`es-ES`, `en-US`…) y la privacidad. Para excluir uno, desactívalo
  en Prowlarr; para castellano, basta con que declare un idioma `es-*`.
- **Jackett**: consulta el indexer `all`; cada resultado trae su indexer real
  en `<jackettindexer>`, así que también se descubre solo.

No hay que editar archivos locales ni listas de nombres. El repositorio no
publica membresías de trackers privados.

## Alta masiva de títulos y fuentes

Los títulos se capturan de TMDB (solo metadatos, no descarga nada):

```bash
python3 -m catalogo.cli tmdb-sync --year 2026 --min-votes 50 --limit 25
```

Los releases se buscan en todos los indexers habilitados de Prowlarr y en
Jackett, con un ritmo bajo:

```bash
python3 -m catalogo.cli bulk-sync --year 2026 --limit 25 --delay 8
```

- `--key movie:tmdb:...` procesa un solo título.
- `--dry-run` busca pero no escribe.
- `--delay` son los segundos entre títulos.
- `--no-jackett` usa solo Prowlarr.
- Para excluir un indexer, desactívalo en Prowlarr o Jackett.

La selección de fuente ordena por salud y después por **tamaño ascendente**,
sin preferencia por 4K. Los trackers privados garantizan la calidad y se
prioriza el menor consumo, tanto en películas como en series y packs.

Idioma por procedencia: los trackers españoles implican castellano
(confianza `tracker`); la etiqueta del título (`CAST`, `LAT`, `VOSE`) tiene
prioridad. Los trackers internacionales quedan en desconocido salvo etiqueta.

Jackett usa `JACKETT_API_KEY` si existe en el entorno; si no, lee la clave de
`~/.config/Jackett/ServerConfig.json`.

### Series

Alta de episodios desde TMDB (elige la serie por año para no confundir anime y
acción real):

```bash
python3 -m catalogo.cli tmdb-sync \
  --tv "One Piece" --tv-year 2023 --seasons 1
```

Captura de fuentes:

```bash
python3 -m catalogo.cli bulk-sync \
  --kind series --year 2023 --limit 8 --delay 8
```

- Solo se aceptan resultados con el `tmdbId` correcto o, si no viene, título y
  año compatibles. Así `One Piece` (2023, 111110) no se mezcla con el anime
  (1999, 37854).
- Se aceptan episodios exactos `SxxExx` y packs de temporada.
- En un pack, el episodio pedido va a prioridad máxima; el resto se descarga
  igualmente en trackers privados.
- El estado usa el progreso del fichero del episodio, no el del pack completo.

## Bibliotecas del catálogo

- `/mnt/catalogo/movies` → biblioteca Jellyfin **Catálogo Películas**.
- `/mnt/catalogo/series` → biblioteca Jellyfin **Catálogo Series**.
- Separadas de las oficiales `Películas` y `Series` de Radarr/Sonarr.

### `.strm` estables y resolución en el proxy

- El `.strm` **no se borra** cuando el título está en Radarr/Sonarr.
- `library_files` guarda los ficheros reales de Radarr/Sonarr; el worker la
  sincroniza en cada ciclo (`sync-library`).
- Al reproducir, el proxy sirve primero el fichero de la biblioteca oficial;
  si no existe, la copia del catálogo; y si no, descarga bajo demanda.
- `bridge` copia al *arr lo que descargó el catálogo (timer cada 30 min).
- `restore-strm` repone los `.strm` que falten (timer diario 04:30).

Exportar por año y tipo:

```bash
python3 -m catalogo.cli export-strm \
  --out /mnt/catalogo --limit 0 --year 2026 --kind movie
python3 -m catalogo.cli export-strm \
  --out /mnt/catalogo --limit 0 --kind series
```

Antes de exportar series legadas, normalizar su título desde TMDB para que
todos los episodios caigan en una sola carpeta de serie:

```bash
python3 -m catalogo.cli normalize-series --delay 0.3
```

### Seed y borrado

Los privados se siembran **hasta que 1fichier borre el fichero**, sin ratio.
Las 120 h son un mínimo, no una fecha de borrado: el worker **no** elimina la
copia al cumplirlas. La regla de seed de qBittorrent es la que manda.

- El `.strm` se queda siempre en la biblioteca.
- El fichero descargado permanece en `/mnt/fichier/descargas` y sigue
  sembrando.
- Si qBittorrent borra el torrent por su regla, el siguiente sync mira el
  tiempo sembrado:
  - si ya se cumplió el mínimo, cierra el compromiso y deja el `.strm`;
  - si se borró antes, marca el compromiso para **reseed** y el worker lo
    vuelve a descargar y sembrar.
- Si 1fichier elimina el fichero, se detectará como ausente y un nuevo play
  lo descargará.
- `evict` es solo una herramienta **manual** para liberar espacio; nunca se
  ejecuta sola. Solo actúa sobre compromisos que ya cumplieron el mínimo:

```bash
python3 -m catalogo.cli evict --limit 10 --execute
```

El worker ejecuta el reseed de los compromisos borrados antes de tiempo con
`--execute-reseed`. Manual:

```bash
python3 -m catalogo.cli reseed --limit 1 --execute
```

Nunca borra rutas de `/mnt/fichier/Peliculas`, `/mnt/fichier/Series` ni
`/mnt/catalogo`.

### Apagones y errores locales

- Si qBittorrent arranca con un torrent `error` o `missingFiles`, el worker
  hace **recheck + resume** y lo reanuda.
- Si el tracker ya no reconoce el torrent, `clean-broken` lo elimina y
  cancela el compromiso.
- Al reproducir, `prepare_download` detecta el error, borra el torrent (sin
  borrar ficheros parciales) y lo vuelve a añadir.
- Si el fichero se perdió en 1fichier, se vuelve a descargar; el `.strm`
  sigue.
- La reparación solo actúa si hay un compromiso activo; después de las 120 h
  no re-descarga sola.

### Torrents que el tracker ya no reconoce

Caso típico: el tracker sube un pack de temporada y los episodios sueltos
dejan de existir. Si qBittorrent recibe un error definitivo
(`not registered`, `torrent not found`, `has been deleted`, etc.):

- se borra el torrent y sus ficheros,
- el release queda `dead`,
- el compromiso se cancela,
- se elimina el `.strm` de la versión concreta,
- el `.strm` por defecto solo se elimina si no queda otra fuente viva.

```bash
python3 -m catalogo.cli clean-broken --limit 10 --execute
```

El worker lo ejecuta con `--execute-clean`.

### Trackers con la descarga rota

Si un índice busca pero no entrega `.torrent` (página de login, torrent
retirado, 404/500 sistemático), se bloquea el tracker completo:

```bash
python3 -m catalogo.cli block-tracker 'TrackerPrivado (API)' --execute
python3 -m catalogo.cli block-tracker 'TrackerPrivado (API)' --unblock --execute
```

Marca sus releases como `blocked` y desactiva su política, sin tocar los
compromisos que ya están sembrando. El catálogo deja de elegirlo para
nuevas descargas. Cada release individual que falle al bajar su `.torrent`
también se marca `dead` automáticamente y se prueba el siguiente candidato;
si no queda ninguno, `/prepare` devuelve `503 {"error":"sin_fuente"}`.

## Plan de crecimiento

Objetivo: catálogo en caliente, sin saturar trackers ni disco.

### Ritmo

- **Cada 6 horas**: `rss-sync` (00, 06, 12, 18) para novedades de
  películas y series.
- **Semanal**: 250 películas del año con más pendientes (2019→2010),
  domingo a las 03:00, más 3-5 series con packs si hacen falta.
- **Mensual**: pasada de salud, limpieza de rotos, revisión de fuentes y
  disco. Los `.strm` no se podan: son estables y el proxy decide la fuente.

### Novedades por RSS

`rss-sync` pide lo último de Prowlarr y Jackett (consulta vacía), resuelve
el `tmdbId` y guarda títulos y releases, tanto películas como series. Los
packs de series se saltan; el fondo de series se hace en los lotes
semanales.

```bash
python3 -m catalogo.cli rss-sync --limit 200 --delay 0.3
```

El servicio lo ejecuta cada 6 horas y exporta los `.strm` nuevos. Jellyfin
tiene monitorización en tiempo real de la biblioteca, así que aparecen sin
esperar a un escaneo manual.

### Fondo antiguo

El lote semanal elige el año con más títulos pendientes y busca 250:

```bash
python3 -m catalogo.cli backfill --limit 250 --delay 8
```

Después exporta el año completo a `/mnt/catalogo`.

### Prioridades

1. Estrenos y novedades del año actual.
2. 2024-2025.
3. 2020-2023.
4. 2010-2019 y anteriores.
5. Series: primero temporadas recientes y packs; después fondo.

### Guardrails

- `--delay 8` en Prowlarr y `0.3` en TMDB.
- Máximo 4 descargas `ondemand` activas (configurable con
  `CATALOGO_MAX_ACTIVE_ONDEMAND`).
- No exportar títulos sin fuente viva.
- Biblioteca primero para **descargas**: no descargar lo que ya esté en
  Radarr/Sonarr. El `.strm` se mantiene y el proxy sirve la copia oficial.
- No bajar de ~500 GB libres en `/mnt/fichier`.
- Seed mínimo 120 h; qBittorrent borra; reseed si se borra antes.
- Limpieza de rotos antes de exportar.

### Seguimiento

- Informe diario: títulos exportados, % de fuentes vivas, rotos eliminados,
  reseeds y espacio libre.

## Política de seed por tracker

No se inventan reglas: se declaran cuando se confirmen.

```bash
python3 -m catalogo.cli set-policy 'TrackerPrivado (API)' \
  --min-seed-hours 120 --target-ratio 0 --keep-for-bonus \
  --abandon-under-percent 5 --abandon-action pause
python3 -m catalogo.cli set-policy 'DivxTotal' \
  --min-seed-hours 0 --target-ratio 0 --no-keep-for-bonus \
  --privacy public --abandon-under-percent 5 --abandon-action delete
python3 -m catalogo.cli policies
```

La función `can_remove` impide borrar un torrent antes de cumplir el tiempo
mínimo o el ratio. El abandono de descargas parciales se rige por
`abandon_under_percent`/`abandon_action` (ver más abajo).

## Admisión: no descargar sin poder sembrar

Antes de añadir un torrent, la puerta comprueba política, salud, peers,
espacio real (descontando lo ya comprometido) y hueco por tracker. Si pasa,
registra un compromiso con `seed_required_until` y `target_ratio`.

```bash
python3 -m catalogo.cli admit 'movie:tmdb:9737' \
  --data-path /mnt/multimedia --reserve-gb 20

python3 -m catalogo.cli intents --limit 20
```

Sin política de tracker, la respuesta es `sin_politica` y **no se descarga**.
Sin un `intent` aprobado, el backend no debe arrancar el torrent.

Cuando ya ha sembrado lo exigido:

```bash
python3 -m catalogo.cli can-finish 1
python3 -m catalogo.cli finish-intent 1
python3 -m catalogo.cli evictable
```

`finish-intent` solo cierra si se cumple la política. `evictable` lista los
compromisos que ya se pueden cerrar; no borra nada por sí solo.

## Salud y peers (Prowlarr en solo lectura)

`health-check` busca en Prowlarr por título/año, compara título normalizado,
tamaño e `info_hash` con el release guardado y actualiza salud/peers. No
descarga `.torrent`, no añade nada a qBittorrent.

```bash
# ver qué haría, sin tocar la base
python3 -m catalogo.cli health-check --limit 5 --dry-run

# verificar un activo concreto
python3 -m catalogo.cli health-check --key 'movie:tmdb:9737' --limit 1

# actualizar un lote pequeño
python3 -m catalogo.cli health-check --limit 5 --delay 1

python3 -m catalogo.cli health
python3 -m catalogo.cli pending-health --limit 20
```

La clasificación es: sin observación `unknown`; observación vieja `stale`;
con peers `alive`; sin peers `dead`; búsqueda sin resultados `dead`.

## qBittorrent en solo lectura

El cliente de lectura (`catalogo/qbittorrent.py`) no implementa `add`,
`delete`, `pause` ni `resume`. La escritura está aislada en
`catalogo/qbit_write.py` y solo se usa desde la admisión y los workers. La
contraseña se pasa por entorno, nunca en código.

```bash
export QBIT_PASS='...'
python3 -m catalogo.cli qbit-version
python3 -m catalogo.cli qbit-torrents --category catalogo
python3 -m catalogo.cli qbit-files <hash>
python3 -m catalogo.cli qbit-properties <hash>
```

`catalogo/torrent_meta.py` calcula el `info_hash` de un `.torrent` local sin
descargarlo, para identificar releases sin fiarse del nombre.

## Sync de qBittorrent (solo lectura)

```bash
export QBIT_PASS='...'
python3 -m catalogo.cli qbit-sync --dry-run
python3 -m catalogo.cli qbit-sync
```

- `torrents/info` se lee una sola vez.
- `torrents/files` solo para `catalogo`/`ondemand`, completos o ya mapeados.
- Los Radarr en `queuedDL` al 0% se ignoran.
- El dedup de Radarr/Sonarr usa `History.DownloadId` y `MovieMetadata.TmdbId`
  / `Series.TmdbId` (con `TvdbId` de respaldo), y solo ficheros completos.
- `cloud_uploaded_at` se rellena con `completion_on` de qBittorrent y
  fallback `now()`.
- **Privados: descarga completa obligatoria.** `allow_partial=0` en sus
  políticas; los parciales se reservan a trackers públicos, si se añaden.
- `max_active` cuenta **descargas simultáneas**, no seeds de 120 h ya
  completados. Un tracker con varios torrents sembrando no bloquea nuevas
  descargas.
- Si `qbit_state == moving` con `progress == 1`, el activo queda en
  `MOVING_TO_1FICHIER` y se lee del origen local (`content_path`) mientras
  termina de aparecer en `/mnt/fichier`. Nunca se marca `missing` por estar
  en tránsito.
- El TTL de 60 días **no** resetea nada por calendario: solo se usa para
  seleccionar qué ficheros comprobar con `stat`; si el fichero existe, sigue
  `ON_1FICHIER`.

### Alcance: solo on-demand

La biblioteca de Jellyfin (`/mnt/fichier/Peliculas` y
`/mnt/fichier/Series`) ya está escaneada por Jellyfin y la gestionan
Radarr/Sonarr. **No se indexa en este catálogo ni se duplica como fuente.**

El flujo normal es: se descarga → Radarr/Sonarr importan a `Peliculas`/`Series`
→ el torrent sigue sembrando en `descargas`. Por eso no se crean releases a
partir de los torrents completos: sería la misma fuente que ya tiene Jellyfin.

El catálogo y el proxy sirven solo para títulos **on-demand que no están en esa
biblioteca**.

## Límites de descarga por categoría

qBittorrent no permite que un torrent supere el límite **global**. Para que
`ondemand` quede sin límite, el global debe estar a `0`; después se limitan
Radarr/Sonarr por torrent.

```bash
QBIT_PASS='...' python3 -m catalogo.cli qbit-limits --dry-run
QBIT_PASS='...' python3 -m catalogo.cli qbit-limits
```

Por defecto:

| categoría | límite |
|---|---:|
| `radarr` | 2500 KiB/s |
| `tv-sonarr` | 2500 KiB/s |
| `ondemand` | 0 (ilimitado) |

Con el máximo de **2 descargas activas** de qBittorrent, Radarr+Sonarr suman
como máximo **5000 KiB/s**. Si se permite más de 2 descargas activas, el total
puede superar 5000. Si solo hay una activa, usa 2500 y deja el resto para
ondemand.

Se puede cambiar por ejecución:

```bash
python3 -m catalogo.cli qbit-limits \
  --radarr-kbps 2500 --sonarr-kbps 2500 --ondemand-kbps 0
```

El límite se aplica por torrent en bytes/s (`0` o `-1` = ilimitado), no por
categoría en qBittorrent. El comando agrupa los hashes y hace una sola llamada
por límite.

### Scheduler

La tarea `qbit-limits` del scheduler interno se ejecuta cada minuto. Se puede
cambiar desde **Configuración → Tareas programadas** o lanzar a mano:

```bash
python3 -m catalogo.cli qbit-limits
```

Alternativa más pesada: `qbit_manage` con `cat_change` en su `config.yml`.
Para este caso el comando ligero hace lo mismo sin añadir otra base de datos ni
limpiezas que puedan tocar el seeding.

## Fallback público (DivxTotal/DonTorrent)

Jackett se usa solo para descubrir releases públicos de fallback. La API key
se pasa por entorno y se elimina de las URLs que se guardan.

```bash
export JACKETT_API_KEY='...'
python3 -m catalogo.cli public-sync --limit 5 --dry-run
python3 -m catalogo.cli public-sync --key 'movie:legacy:2001' --limit 1
```

- `DivxTotal` y `DonTorrent` entran como `source='jackett'`,
  `privacy='public'`, `health='alive'`, con `download_url` sin API key.
- Políticas públicas: `min_seed=0`, `ratio=0`, `allow_partial=1`,
  `keep_for_bonus=0`. La admisión las permite sin compromiso de seed.
- Privados: 120 h mínimo, descarga completa (`allow_partial=0`) y
  `keep_for_bonus=1`; se mantienen hasta que 1fichier borre.

## 3A.1: play_intent + escritura controlada

- `HEAD` nunca arranca nada: en frío devuelve 503 sin intención.
- `GET`/`Range` en frío **sin `download=1`** → 403 `download_not_allowed`,
  sin `play_intent` y sin tocar qBittorrent.
- `GET + Range + download=1` en frío → crea `play_intent`; sin backend real
  devuelve 503 `backend_disabled`.
- `backend.prepare_download` exige `play_intent` + `seed_commitment` + política.
- El torrent se añade **paused**; solo después se hace `forceStart` y `resume`.
  El `forceStart` es **solo para la descarga**: al llegar al 100 % se retira
  para que qBittorrent lo siembre con las reglas normales de cola/ratio.
- Si el torrent **ya está añadido y descargando**, `/play` no lo pausa ni lo
  reanuda otra vez: solo ajusta prioridades. El arranque se hace una sola vez.
  Así se evitan announces duplicados y el bloqueo temporal del tracker
  (`Please respect the min interval`).
- **Fallback automático**: si un release no consigue pares en 3 s (o datos en
  15 s), el proxy lo pausa (privado) o lo borra si es público y casi no
  descargó, lo marca como fallido durante 5 min y prueba el siguiente. El
  orden es: release pedido, misma calidad, tamaño ascendente y seeders. Hasta
  4 intentos. Si todos fallan, `/play` devuelve `503 all_sources_failed` con
  la lista de intentos.
- No se espera al reannounce del tracker para reproducir: si el release no
  arranca, se pasa al siguiente.
- Privados: todo el pack a prioridad 1 y el `SxxEyy` pedido a 7.
- Públicos: solo el fichero objetivo a 1, el resto a 0.
- Cupo de 2 `ondemand` activos.

## Movimiento de privados a 1fichier

Flujo de almacenamiento:

```
temp:  /mnt/cache/tmp
save:  /mnt/descargas/ondemand
final: /mnt/fichier/descargas
```

- qBittorrent mueve de temp a save al 100%.
- `move-completed` mueve un privado completo de `/mnt/descargas/ondemand`
  a `/mnt/fichier/descargas` **solo si no hay stream activo**.
- El movimiento a 1fichier es **automático**: la tarea `worker-cycle` del
  scheduler ejecuta cada 5 minutos el equivalente a
  `worker --once --execute-moves --no-debrid`. Primero sincroniza qBittorrent
  y después mueve los privados completos.
- Los públicos no se mueven.
- qBittorrent sigue sembrando desde 1fichier; el compromiso de seed no cambia.
- El lease de streaming dura 120 s por defecto y se renueva con cada `Range`.
- `move-completed` es dry-run por defecto; para ejecutar:

```bash
QBIT_PASS='...' python3 -m catalogo.cli move-completed
QBIT_PASS='...' python3 -m catalogo.cli move-completed --execute
```

- Públicos no se mueven por este worker.
- Tras el movimiento, qBittorrent siembra desde 1fichier y el sync lo verá
  como `MOVING_TO_1FICHIER` → `ON_1FICHIER`.

El resolver da prioridad a `media_items.active_file_id`: si el activo está
archivado en 1fichier, sirve esa copia aunque el `play_target` antiguo siga
apuntando a un release.

## Debrid público (Real-Debrid, avanzado)

**No es necesario para el uso normal.** Solo se activa si defines
`RD_API_KEY` en el entorno; no se configura desde la web. Se usa con
`privacy='public'` y nunca con privados.

```bash
python3 -m catalogo.cli debrid-check --limit 5 --dry-run
python3 -m catalogo.cli debrid-check --limit 5
```

- Add-and-check con `RD_API_KEY` (en `.env`, 600 e ignorado por git).
- Si no está cacheado, se borra el torrent de RD y no se usa.
- Si está cacheado, se guarda `debrid_link`; el resolver responde `302` a
  ese enlace.
- Recheck: diario si cacheado, 7 días si no.

## Estado y buffer (`/status`)

La app puede consultar el progreso sin arrancar nada:

```http
GET /status?key=movie:tmdb:1236045
GET /status?key=movie:tmdb:1236045&release=12827
```

Devuelve, entre otros:

```json
{
  "state": "DOWNLOADING",
  "source": "torrent",
  "release_id": 12827,
  "quality": "1080p",
  "tracker": "TrackerPrivado (API)",
  "progress": 0.14,
  "downloaded_bytes": 286000000,
  "size_bytes": 2045202920,
  "speed_bps": 26500000,
  "eta_seconds": 66,
  "seeds": 4,
  "peers": 0,
  "swarm_seeds": 19,
  "stream_ready": false,
  "seed_state": "active",
  "seed_required_until": "2026-11-24T07:00:14Z"
}
```

- `state`: `COLD`, `DOWNLOADING`, `PAUSED`, `SEEDING`, `AVAILABLE`,
  `DEBRID`, `ERROR` o `UNAVAILABLE`.
- `stream_ready`: `true` si `/play` puede servir ya (local, 1fichier, debrid
  o torrent completo).
- Sin `release`, devuelve la versión más relevante; con `release`, la pedida.
- Es solo lectura: no crea `play_intent`, no arranca torrents y no toca
  qBittorrent.
- La app puede hacer polling cada 2 s para el indicador de buffer.

### Lookup de `.strm` para la app

La APK recibe de Jellyfin el `Path` del `MediaSource` y lo pasa al proxy:

```http
GET /catalog/lookup?path=/mnt/catalogo/movies/9 lunas 2026 (2026)/9 lunas 2026 (2026).strm
```

Respuesta si es del catálogo:

```json
{
  "is_catalog": true,
  "key": "movie:tmdb:1447420",
  "release_id": 109,
  "status_url": "/status?key=movie%3Atmdb%3A1447420&release=109",
  "play_url": "/play?key=movie%3Atmdb%3A1447420&release=109&download=1",
  "status": {
    "state": "SEEDING",
    "progress": 1.0,
    "stream_ready": true
  }
}
```

Si no es un `.strm` del catálogo o está fuera de las rutas permitidas:

```json
{ "is_catalog": false, "reason": "path_no_permitido" }
```

- Solo lee ficheros `.strm` dentro de `CATALOGO_STRM_ROOTS`
  (`/mnt/catalogo:/mnt/palantir` por defecto).
- No arranca descargas.
- `status_url` y `play_url` son relativos; la APK les añade su base del proxy.
- Flujo APK: `PlaybackInfo` → `MediaSources[].Path` → `/catalog/lookup` →
  si `is_catalog`, overlay y polling a `status_url` → cuando
  `stream_ready` sea `true`, reproducir con Jellyfin normal.
- Para transcodificación, subtítulos y progreso de Jellyfin, reproducir por
  Jellyfin; `play_url` es solo para reproducción directa/diagnóstico.

### Acceso desde la APK

TOD escucha en `0.0.0.0:8773`, así que Jellyfin (mismo host) y una APK en la
LAN o WireGuard pueden usar la misma base: `http://IP_DEL_SERVIDOR:8773`.
Los `.strm` existentes siguen usando `127.0.0.1:8773` para Jellyfin.

- `/status` y `/catalog/lookup` son de solo lectura.
- `/prepare`, `/lease` y `/play?...&download=1` están restringidos a loopback.
- El panel web exige `Host` permitido; añade la IP LAN/WireGuard en
  **Configuración → Seguridad** si cambia.
- Para fuera de casa, usa WireGuard o un reverse proxy con TLS.

## Blindaje de Jellyfin

Jellyfin trata los `.strm` como accesos directos y puede sondearlos al
escanear o al abrir un título. Para que nunca arranque un torrent:

- Todo `.strm` exportado apunta a `/play?...&download=0`.
- `/play` sin `download=1` es **solo lectura**.
- Si la fuente es un torrent frío, responde `403 download_not_allowed` y **no**
  crea `play_intent`, no llama a la admisión y no toca qBittorrent.
- `HEAD` tampoco arranca nunca.
- Las versiones locales, en 1fichier o con debrid cacheado se reproducen
  igual, con o sin flag.

La app debe añadir `download=1` de forma explícita cuando el usuario pida
descargar una versión fría.

## Elegir versión desde la app

- `GET /sources?key=movie:tmdb:438631` devuelve todas las versiones vivas:
  `release_id`, tracker, calidad, audio, tamaño, seeders, salud, privacidad y
  si hay debrid.
- `GET /play?key=...&release=<release_id>` reproduce una versión disponible.
- `GET /play?key=...&release=<release_id>&download=1` arranca la descarga si
  la versión está fría.
- La app puede recorrer `sources` y probar `play` hasta que una devuelva `206`.
- No se exponen `download_url` ni enlaces de debrid en `/sources`.

Orden de selección:

1. salud (`alive` antes que `unknown`/`stale`);
2. calidad (`4K` → `1080p` → `720p`);
3. dentro de la misma calidad, **tamaño ascendente**;
4. seeders como desempate.

`CATALOGO_MAX_STREAM_GB` está a `0` por defecto: no bloquea ni penaliza
tamaños. Si se pone un valor mayor que 0, `/sources` marca `too_large` en los
releases que lo superan, pero la admisión no los rechaza: sigue sirviendo para
que la app decida. El orden no cambia.

## Abandono de descargas (<5 %)

Un torrent añadido bajo demanda que lleva más de `--min-age` segundos
descargando, sigue por debajo del umbral de su tracker y no tiene ninguna
lease de reproducción reciente se considera abandonado.

```bash
# ver qué haría (dry-run por defecto)
QBIT_PASS='...' python3 -m catalogo.cli abandon

# ejecutar
QBIT_PASS='...' python3 -m catalogo.cli abandon --execute
```

Reglas:

- Solo torrents de categoría `ondemand`, incompletos y sin stream activo.
- El umbral se lee de `tracker_policies.abandon_under_percent`; `0` desactiva
  el abandono para ese tracker.
- `abandon_action` puede ser `keep`, `pause` o `delete`.
- **Privados:** aunque la política diga `delete`, se degrada a `pause` para no
  provocar un hit-and-run. Solo borra con `--private-delete` (o
  `--abandon-private-delete` en el worker).
- **Públicos:** `delete` borra el torrent y los ficheros parciales.
- Nunca toca torrents completos ni versiones archivadas en 1fichier.

## Siembra normal (sin force)

Durante la descarga se usa `forceStart` para no esperar la cola. Al completar,
el worker retira el force para que el torrent quede en `uploading`,
`stalledUP` o `queuedUP`, según las reglas normales de qBittorrent.

```bash
# ver cuáles siguen en forcedUP
QBIT_PASS='...' python3 -m catalogo.cli normalize-seeding

# quitarlo de verdad
QBIT_PASS='...' python3 -m catalogo.cli normalize-seeding --execute
```

Solo afecta a torrents del catálogo (con release vinculado o categoría
`ondemand`) que ya están completos. Las descargas en curso mantienen el force
hasta terminar. No toca límites de ratio ni de tiempo: si qBittorrent tiene
límites globales, se aplicarán como a cualquier otro torrent.

## Worker automático

```bash
QBIT_PASS='...' python3 -m catalogo.cli worker --once --execute-moves --no-debrid
```

Un ciclo:

1. limpia `stream_leases` viejos;
2. `qbit-sync` en solo lectura;
3. `abandon` en **dry-run** salvo `--execute-abandon`;
4. `move-completed` en **real** con `--execute-moves`;
5. `normalize-seeding` en **dry-run** salvo `--execute-seeding`;
6. `debrid-check` desactivado con `--no-debrid`.

El scheduler interno ejecuta ese ciclo cada 5 minutos (`worker-cycle`) y es el
que mueve los privados completos a 1fichier. No toca Jellyfin ni reinicia nada.
Se puede cambiar la cadencia o desactivar desde **Configuración → Tareas
programadas**; el botón **Ejecutar ahora** lanza el mismo ciclo.

Los antiguos `catalogo-worker.service` y `catalogo-worker.timer` quedan
desactivados por `deploy/install-unified.sh`.

Para ejecutar el abandono o la normalización de siembra de verdad, hazlo a
mano:

```bash
QBIT_PASS='...' python3 -m catalogo.cli worker --once --execute-abandon
QBIT_PASS='...' python3 -m catalogo.cli worker --once --execute-seeding
```

## Servicio único `tod.service`

TOD sustituye a `catalogo-proxy` + `catalogo-web` + timers. Un solo proceso
sirve la web, la API, la reproducción y el scheduler en `0.0.0.0:8773`.

- Unidad: `/etc/systemd/system/tod.service`
- Plantilla versionada: `deploy/tod.service.in`
- Instalador: `deploy/install-unified.sh`
- Entorno de arranque: `/etc/catalogo.env` (`root:tu_grupo`, `0640`)
- Plantilla: `deploy/catalogo.env.example`
- Arranca después de `qbittorrent.service`

Instalación o reinstalación:

```bash
sudo install -m 640 -o root -g "$USER" /dev/null /etc/catalogo.env
sudo editor /etc/catalogo.env          # QBIT_PASS=...
sudo bash deploy/install-unified.sh
```

El instalador desactiva los servicios y timers antiguos para evitar dobles
ejecuciones.

Gestión:

```bash
sudo systemctl status tod.service
sudo systemctl restart tod.service
sudo systemctl stop tod.service
sudo journalctl -u tod.service -f
```

Al arrancar debe mostrar:

```
TOD en http://0.0.0.0:8773 db=/opt/catalogo/var/catalogo.db (... web+reproducción+scheduler ...)
```

Si aparece `solo local`, el login de qBittorrent falló. Se puede corregir desde
**Configuración → qBittorrent** sin editar el entorno. Las versiones ya
archivadas en 1fichier se siguen sirviendo en ese modo.

Si reinicias qBittorrent, reinicia también `tod.service` para renovar la
cookie de sesión:

### Puertos locales

| puerto | uso |
|---:|---|
| 8765 | `meyectl` (DietPi) |
| 8766 | `palantir3-proxy` |
| 8771 | `palantir3-catalog-stream` (unidad habilitada, inactiva) |
| 8772 | `palantir3-assets-resolver` (unidad habilitada, inactiva) |
| **8773** | **`tod.service`** (web + reproducción + scheduler) |
| 8774 | `castellano-resolver` (pruebas) |
| 8086 | Jellyfin |
| 1340 | qBittorrent |

Comprobación rápida antes de arrancar otro proxy:

```bash
sudo ss -ltnp | grep -E ':(8765|8766|8771|8772|8773|8774)\b'
```

`8773` no choca con los proxies de Palantir ni con las pruebas de Castellano.
Si en el futuro se ocupa, cambia `--port` en la unidad y la `base-url` de los
`.strm`.

## Panel web doméstico (LAN y WireGuard)

Consola de administración del **catálogo on-demand** para la red de casa.
No sustituye a Jellyfin y no gestiona Radarr/Sonarr: de ellos solo lee la
biblioteca, la cola y los discos. No expone `download_url`, claves de API ni
enlaces de debrid.

Acceso:

- En casa: `http://IP_LAN:8773`
- Por WireGuard: `http://IP_WIREGUARD:8773`

El servicio escucha en `0.0.0.0:8773` para cubrir ambas interfaces y sirve
también la reproducción. No publiques el puerto en Internet: para fuera de
casa, WireGuard.

Pantallas:

- **Inicio**: estado de la biblioteca, actividad reciente, descargas
  on-demand, espacio y biblioteca de Radarr/Sonarr en solo lectura.
- **Añadir**: búsqueda en TMDB con carteles, alta de película o temporada y
  dos acciones: **Añadir a la biblioteca** (genera el `.strm`) o
  **Forzar descarga**.
- **Catálogo**: pósters y series agrupadas por título, con pestañas
  **En biblioteca / Pendientes / Sin fuente**, filtros por estado y tipo,
  vista de carteles o lista, selección múltiple y acciones en lote.
  Los episodios se navegan dentro de la ficha de la serie. La ficha se
  construye con TMDB (todas las temporadas y episodios) y se cruza con el
  estado local: lo que aún no está en la base aparece como **sin catálogo**
  y se importa al añadir la serie o la temporada a la biblioteca. En **Pendientes**
  hay un botón **Buscar todas las pendientes** que lanza un trabajo de
  búsqueda de fuentes para todo el filtro actual, no solo la página visible;
  el modal permite limitar el lote y ajustar la pausa.
- **Descargas**: solo la categoría `ondemand`, agrupada por título y con
  filtros por estado, selección múltiple y el límite de descargas.
- **Tareas**: lanzador agrupado por categoría, marca de tareas peligrosas,
  tarea en curso e historial legible.
- **Siembra**: compromisos, ratio frente al objetivo y cierre permitido.
- **Estado**: servicios, integraciones, discos, trackers y auditoría.
- **Técnico**: catálogo interno de releases, salud y disponibilidad.

Los contadores de Inicio salen de los `.strm` reales de `/mnt/catalogo`, no
de la base. Radarr/Sonarr se consultan por API y solo para datos.

Los pósters y sinopsis se guardan en `media_items` (migración
`021_media_metadata`) y se rellenan desde TMDB con la tarea **Actualizar
metadatos** o el comando:

```bash
python3 -m catalogo.cli metadata --workers 6
```

La consola arranca en modo oscuro. El conmutador de tema de la cabecera
alterna entre oscuro y claro y guarda la preferencia en el navegador.

### Búsqueda manual de fuentes

En la ficha de un título, pestaña **Fuentes**, el botón **Buscar
manualmente** consulta Prowlarr y Jackett en ese momento, guarda los
candidatos y muestra una tabla con calidad, tamaño, seeds, salud y acciones
para fijar, verificar o preparar la descarga. El nombre de búsqueda es
editable para títulos cuyo release usa otro nombre. No descarga nada por sí
sola.

### Quitar de la biblioteca

En la ficha de un título o de una serie aparece **Quitar de la biblioteca**
cuando hay `.strm`. Elimina únicamente esos ficheros `.strm`; no borra la
ficha del catálogo, las descargas ni los torrents.

### Biblioteca y enlaces

La pertenencia a la biblioteca es independiente de que exista un `.strm`. Una
película o una serie se puede añadir a la biblioteca aunque todavía no haya
ninguna fuente; la ficha queda como **En biblioteca, sin enlace**. Los enlaces
se generan cuando aparece una fuente y, si se decide **monitorizar**, la
búsqueda diaria se encarga de encontrarla.

Quitar de la biblioteca elimina los `.strm` y desactiva el seguimiento, pero no
borra fichas, descargas ni torrents.

### Colecciones y sagas

Si TMDB conoce la colección de una película, su ficha muestra un enlace a la
saga. La página de la colección lista todas las películas con su estado local
y permite **añadir al catálogo** las que falten o **añadir toda la colección
a la biblioteca**. Las películas que ya existen no se duplican.

### Seguimiento diario

La pertenencia a la biblioteca es independiente de que exista un `.strm`.
Cuando se marca **monitorizar**, el comportamiento sigue el de Sonarr/Radarr:

- búsqueda inmediata al añadir,
- el título queda monitorizado indefinidamente hasta que aparece el `.strm`
  o se desactiva el seguimiento,
- no se busca antes de la fecha de estreno o emisión,
- no se busca si ya hay una descarga activa,
- las novedades se capturan por RSS cada **30 minutos**, igual que el
  `RssSync` de Radarr (Sonarr usa 15),
- el barrido diario de las **04:20** busca solo títulos ya disponibles y
  empieza por los que llevan más tiempo sin buscarse.

No hay un máximo de intentos ni una fecha límite. El título permanece
monitorizado, como en Radarr/Sonarr, y se detiene cuando el `.strm` ya existe.

El scheduler interno ejecuta ese barrido a diario a las 04:20 (`monitor`) y la
captura de novedades cada 30 minutos (`rss-sync`). También se puede lanzar el
barrido desde **Tareas → Buscar seguimiento** o con:

```bash
python3 -m catalogo.cli monitor --limit 100 --delay 2
```

Al añadir una colección a la biblioteca, las películas sin fuente quedan en
seguimiento en lugar de fallar silenciosamente.

La tarea no bloquea el panel, prioriza por popularidad y se puede relanzar
para completar los títulos que fallaron.

### Vista de seguimiento

**Biblioteca → Seguimiento** reúne los títulos monitorizados que todavía no
tienen `.strm`, con filtros por tipo y estado:

- **Sin fuente**: ya disponibles, sin enlace.
- **Pendiente de estreno**: monitorizados, pero aún sin fecha de estreno o
  emisión.
- **Descargando**: con una descarga activa en la cola.

La misma vista muestra la salud de RSS, el último barrido diario y las
estadísticas de Prowlarr por indexer: consultas, fallos y tiempo de respuesta.
También permite buscar un título concreto, lanzar una búsqueda de todos los
disponibles y pausar o reactivar un indexer problemático desde el propio panel.

### Límite on-demand y forzar descarga

El límite de descargas simultáneas del catálogo se guarda en la tabla
`settings` (migración `019`) y se puede cambiar desde **Descargas**, de 1 a
10. Se aplica sin reiniciar y solo cuenta torrents de categoría `ondemand`.

**Forzar descarga** es una acción manual:

- busca fuentes si hace falta,
- crea la descarga bajo admisión,
- hace `resume` y `setForceStart=true` en qBittorrent,
- se salta la cola global ocupada por Radarr/Sonarr y el límite on-demand.

No cambia `max_active_downloads` de qBittorrent ni la configuración de
Radarr/Sonarr.

Las tareas largas se guardan en la tabla `jobs` (migración `018`) y se
ejecutan de una en una. Las acciones destructivas piden confirmación y las
mutaciones quedan auditadas en `events`. Los borrados de ficheros en
trackers privados siguen prohibidos desde el panel.

### Acceso y seguridad

- Pensado para uso doméstico: **sin login por defecto** en LAN/WireGuard.
- Si algún día quieres login, define `CATALOGO_WEB_TOKEN` en
  `/etc/catalogo.env`; el panel pedirá el token y guardará una cookie firmada.
- Las mutaciones exigen la cabecera `X-Catalogo: web`, lo que evita CSRF.
- El panel solo acepta `Host` de la IP LAN, WireGuard, localhost o el
  hostname del servidor (`CATALOGO_WEB_ALLOWED_HOSTS` para añadir más).
- No hay TLS propio: el tráfico exterior va cifrado por WireGuard.

### Servicio systemd

El panel ya no tiene unidad propia: se instala con el servicio único.

```bash
sudo bash deploy/install-unified.sh
sudo systemctl status tod.service
```

El token y los hosts permitidos se pueden cambiar desde
**Configuración → Seguridad**. El entorno sigue sirviendo de fallback:

```bash
CATALOGO_WEB_ALLOWED_HOSTS=IP_LAN,IP_WIREGUARD
# CATALOGO_WEB_TOKEN=...   # opcional
```

Si cambia la IP LAN, actualiza los hosts permitidos en la web y reinicia
`tod.service`.

## Siguiente fase

1. Exportar solo versiones disponibles a Jellyfin y limpiar los `.strm`
   fríos; el servidor ya los bloquea, pero así evitamos errores 403 al
   sondear. El resto de versiones queda en `/sources` para la app.
2. Encontrar y validar un release público cacheado en Real-Debrid.
3. Limpiar las bibliotecas duplicadas de Jellyfin una vez sin reproducción
   activa.
4. Indicador de buffer en la app del usuario (fuera de este repositorio).

## Repositorio local

El proyecto está en un repositorio git **solo local**, sin remoto:

```bash
cd /opt/catalogo
git log --oneline
git status
```

`var/` está ignorado: contiene bases y previsualizaciones, no se versiona.
