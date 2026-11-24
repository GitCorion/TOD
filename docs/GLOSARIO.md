# Glosario de la interfaz

La interfaz habla de lo que ve el usuario, no de cómo lo llama el motor.
Estos son los únicos términos que deberían aparecer en pantalla.

| En la interfaz | Qué significa | Nombre interno |
|---|---|---|
| **Disponible** | El título ya está en Jellyfin y se puede reproducir: tiene `.strm` y fuente viva o fichero. | `publicado` |
| **Pendiente** | Hay una fuente o un fichero, pero todavía no existe el `.strm`. | `pendiente` |
| **Sin fuente** | No hay ningún release que se pueda reproducir. | `sin_fuente` |
| **En seguimiento** | TOD buscará fuentes para ese título y lo añadirá a Jellyfin cuando aparezcan. | `monitored` |
| **Sin estrenar** | El episodio aún no se ha emitido; no cuenta como ausente ni incompleto. | `available=false` |
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
- Para el resultado visible, decir **Disponible**. Para lo que tiene fuente pero
  no está en Jellyfin, **Pendiente**. Un `.strm` sin nada que reproducir es una inconsistencia transitoria: se
  muestra **Sin fuente** y la reconciliación lo retira. Para un episodio futuro, **Sin estrenar**.
- Para el seguimiento automático, decir **En seguimiento**, **Seguir** y
  **Dejar de seguir**.
- `.strm` solo aparece cuando se habla del fichero en sí (por ejemplo, la
  carpeta que lee Jellyfin). Para el estado se habla de **Disponible** o
  **Pendiente**; Jellyfin se nombra cuando se habla de la integración, la carpeta
  o la acción de añadir.
- Jellyfin, Radarr, Sonarr, qBittorrent, Prowlarr, Jackett y TMDB se escriben
  con su mayúscula oficial.
