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

## Seguimiento por defecto

Todo título que **no esté en Radarr/Sonarr** se marca como monitorizado al
entrar en TOD. Así se busca una fuente y se publica su `.strm` sin tener que
pedirlo a mano. Al publicarse el `.strm`, el seguimiento se cierra solo.

`library_added` es la fuente de verdad de pertenencia a los *arr. Si un título
sale de Radarr/Sonarr, vuelve a seguimiento; si entra, deja de perseguirse
desde TOD. Los títulos que ya tienen fichero local/torrent también se marcan
al importarse, para que no queden fuera de la reconciliación.

## Reproducción

`.strm` estable:

```text
http://127.0.0.1:8773/play?key=movie:tmdb:9737&download=0
```

- `download=0`: uso normal desde Jellyfin. Si el `.strm` está frío, un
  `GET` con `Range` (un reproductor real) autoriza el prepare; un escaneo
  sin `Range` sigue recibiendo 403.
- `download=1`: fuerza el arranque desde la app o un cliente explícito.
- `/status?key=...`: progreso, velocidad, seeds y `stream_ready`.
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
  si lo para o lo borra.
- El borrado desde TOD está desactivado por defecto. Se puede activar en
  **Configuración → Siembra**; entonces aparece el botón de borrar en
  Descargas, con opción de quitar solo el torrent o borrar también ficheros.
- Al descubrir un tracker privado sin política, TOD crea una inicial con los
  valores de **Configuración → Siembra** (por defecto 72 h y ratio 1,0;
  cada instalación puede cambiarlos).
  También se puede cambiar por CLI:
  `tod set-policy Tracker --min-seed-hours 24 --target-ratio 0.5`.
- Públicos: sin compromiso de seed.
- `evict`, `reseed`, `abandon`, `clean-broken` y `move-completed` ya no se
  usan: el borrado pertenece a qBittorrent.

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
**Sistema → Tareas → Actualizar metadatos**, marcando «Revisar también los ya
rellenados». Sin ese paso, los títulos importados de Radarr/Sonarr no tienen
país y el latino se queda como respaldo.

## Almacenamiento

Valores por defecto en una instalación systemd:

```text
descargas:        /mnt/descargas
on-demand:        /mnt/descargas/ondemand
archivo:          /mnt/library/archive
biblioteca .strm: /mnt/catalogo
```

En Docker se usan `/downloads`, `/library` y `/media/catalogo` dentro del
contenedor, montados desde `./data/`.

Las rutas se cambian en **Configuración → Directorios**. TOD valida que
existan y sean escribibles; no mueve datos por su cuenta.

TOD **no recorre las bibliotecas de Radarr/Sonarr**. `library_files` se
reconstruye desde sus APIs y `qbit-sync` mantiene el estado de los torrents;
las rutas de archivo se dan por buenas sin hacer `stat` por red. Solo se
comprueba el disco local de descargas y el fichero concreto cuando se sirve
una reproducción. Así no se duplica el trabajo del *arr.

Si Radarr y Sonarr comparten un directorio padre, ponlo en **Carpeta común**
(por ejemplo `/mnt/fichier`). TOD lo usa para reconocer todo lo que vive en la
biblioteca de red, incluidas las descargas que qBittorrent guarda allí.

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
