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
- El worker mueve privados completos a `/mnt/library/archive` si no hay stream activo.

```bash
tod qbit-version
tod qbit-torrents --category ondemand
tod qbit-sync --dry-run
tod qbit-limits
```

## Seed y espacio

- Privados: seed mínimo por política; no se borra al cumplir las horas.
- Al descubrir un tracker privado sin política, TOD crea una inicial de
  72 h y ratio 1,0. Se puede cambiar:
  `tod set-policy Tracker --min-seed-hours 24 --target-ratio 0.5`.
- Públicos: sin compromiso de seed.
- `evict` es manual y solo actúa sobre compromisos ya cumplidos.

```bash
tod policies
tod evictable
tod reseed --limit 1 --execute
tod evict --limit 10 --execute
```

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
| Sin espacio | `df -h`, límite on-demand y `evictable` |
| Tarea atascada | `Tareas` en la web; `jobs` guarda estado y error |
