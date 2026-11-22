# Glosario de la interfaz

La interfaz habla de lo que ve el usuario, no de cómo lo llama el motor.
Estos son los únicos términos que deberían aparecer en pantalla.

| En la interfaz | Qué significa | Nombre interno |
|---|---|---|
| **En Jellyfin** | El título aparece en Jellyfin porque TOD ya creó su `.strm`. | `publicado` |
| **Falta en Jellyfin** | Hay una fuente reproducible, pero todavía no existe el `.strm`. | `pendiente` |
| **Sin fuente** | No hay ningún release que se pueda reproducir. | `sin_fuente` |
| **En seguimiento** | TOD buscará fuentes para ese título y lo añadirá a Jellyfin cuando aparezcan. | `monitored` |
| **Fuente** | Un release o torrent que se puede reproducir. | `release` |
| **Carpeta de los `.strm`** | Carpeta donde TOD escribe los ficheros que lee Jellyfin. | `catalog_library` |

## Trackers

- **Descargar** solo permite o bloquea descargas nuevas de ese tracker.
- **Bloquear** marca sus releases como no disponibles y permite borrar sus
  `.strm`. El principal solo se borra si el título se queda sin otra fuente ni
  fichero local.

## Reglas de escritura

- No usar **publicar**, **publicación**, **publicado** ni **monitorizar** en la
  interfaz. Son nombres del motor y no explican nada al usuario.
- Para el resultado visible, decir **En Jellyfin**. Para lo que falta, decir
  **Falta en Jellyfin**.
- Para el seguimiento automático, decir **En seguimiento**, **Seguir** y
  **Dejar de seguir**.
- `.strm` solo aparece cuando se habla del fichero en sí (por ejemplo, la
  carpeta que lee Jellyfin). Para el estado, se habla de Jellyfin.
- Jellyfin, Radarr, Sonarr, qBittorrent, Prowlarr, Jackett y TMDB se escriben
  con su mayúscula oficial.
