# Integración nativa de Jellyfin con TOD — investigación

Fecha: 2026-11-24

## Objetivo

Conseguir que un cliente Jellyfin nativo (probado con **Jellyfin iOS 1.8.0**)
reproduzca los `.strm` del catálogo TOD sin depender de Medusa, manteniendo el
flujo de Medusa intacto.

Criterios:

- Arranque en frío aceptable: preparar el torrent y empezar a servir sin que
  el cliente se quede en un spinner indefinido.
- Sin regresiones en Medusa 2.4.2-beta.6.
- Sin tocar el `.strm` ni el `Path` que usa Medusa para resolver el `release`.
- Poder revertir con el flag `NativeMode` o restaurando el DLL anterior.

## Entorno

| Elemento | Valor |
|---|---|
| Jellyfin | 12.4, host, puerto 8086 |
| Plugin | `Jellyfin.Plugin.Catalogo` 1.1.4.0 |
| TOD | 0.3.2, puerto 8773 |
| Cliente | Jellyfin iOS 1.8.0 |
| Biblioteca | `/mnt/catalogo` (TOD), `/mnt/stream` (otra) |
| Trackers activos | 1337x, HD-Olimpo, eMuwarez, DonTorrent, DivxTotal, Milnueve, Torrenteros |
| Trackers desactivados | LimeTorrents, YTS, The Pirate Bay, TorrentGalaxyClone, etc. |

## Resumen ejecutivo

Sí, hay una causa raíz clara para el síntoma «primer fotograma + spinner +
emisión en vivo»:

Jellyfin genera un HLS **en directo** (`live.m3u8`, playlist `EVENT`) cuando
`state.RunTimeTicks` es `null`. Eso ocurre si el estado de transcodificación no
ve la duración del ítem. La respuesta de `PlaybackInfo` sí lleva
`RunTimeTicks`, pero el `.strm` de la biblioteca no tiene la duración
persistida; el plugin solo la asigna en memoria en `SaveStreams`.

La condición exacta está en Jellyfin:

```csharp
// MediaBrowser.Controller/MediaEncoding/EncodingJobInfo.cs
public bool IsSegmentedLiveStream
    => TranscodingType != TranscodingJobType.Progressive && !RunTimeTicks.HasValue;
```

```csharp
// Jellyfin.Api/Helpers/DynamicHlsHelper.cs
var isLiveStream = state.IsSegmentedLiveStream;
var baseUrl = isLiveStream ? "live.m3u8" : "main.m3u8";
```

Por eso el master playlist devuelve `live.m3u8` y el cliente iOS lo pinta como
emisión en vivo, con spinner, aunque el transcode esté generando segmentos.

## Qué se intentó

### Plugin

1. Documentar el contrato real de Medusa (`docs/MEDUSA-CONTRACT.md`).
2. Añadir tests de caracterización de `Capabilities`, `Status`, `Prepare`,
   `Lease` y de las URLs del proxy.
3. Añadir `NativeMode`, apagado por defecto.
4. Detectar Medusa por `X-Emby-Client` y `Authorization`/`X-Emby-Authorization`
   (`Client="Medusa"`). Si no hay contexto HTTP, se usa compatibilidad.
5. Separar `PrepareTimeoutSeconds` (40 s) del timeout normal (15 s), porque
   `/prepare` de TOD puede tardar más que un `/status`.
6. Añadir `NativePrebufferSeconds` (0 por defecto; se probó con 5 y 12).
7. En modo nativo, forzar `SupportsDirectPlay = false` para los `.strm` del
   catálogo, para que el cliente no intente abrir `127.0.0.1:8773` directamente.
8. Tests: 50 pasan.

Commits del plugin:

- `6578789` Documenta el contrato con Medusa.
- `b74e608` Tests de caracterización.
- `d4d8280` `NativeMode` y detección de Medusa.
- `5c7aea5` Tests de la decisión de preparar.
- `bc22d60` Prebuffer nativo opcional.
- `5dbe769` Documentación del modo nativo.
- `fea01f3` Timeout de prepare separado.
- `ff91519` Fuerza proxy de Jellyfin para `.strm` en modo nativo.

### TOD

1. Limpiar el cooldown de una fuente fallida si el torrent ya tiene progreso.
2. Hacer que `/play` espere buffer cuando el `.strm` ya está preparado, en vez
   de devolver 503 por una carrera de arranque.
3. Tests: 499 pasan.

Commits de TOD:

- `78e7bed` Reintenta una fuente fallida si el torrent ya descarga.
- `bf01478` Espera buffer en `/play` ya preparado en lugar de fallar.

### Configuración desplegada

- `NativeMode = true`
- `NativePrebufferSeconds = 0`
- `PrepareTimeoutSeconds = 40`
- `TimeoutSeconds = 15`

## Fallos observados

| Caso | Qué pasó | Causa |
|---|---|---|
| **Negociador** | `/prepare` 15,82 s, `/play` 503, FFmpeg code 8 | 0 seeds; la fuente no tenía datos |
| **Good Burger 2** | `/prepare` 87 s, 503; 24 resultados | Todos de trackers desactivados; los de 1337x muertos |
| **Battle Over Britain** | `/prepare` 33 s, `tracker_blocked` | Release de 1337x pasó a `dead` |
| **El soldado desconocido** | `/prepare` 10 s, `tracker_blocked` | Release de 1337x pasó a `dead` |
| **Fallout S02E03** (primera prueba) | `/play` 503 `sin_fuente`, `attempts: []` | Cooldown de 5 min tras fallo transitorio; corregido |
| **Tierra de mafiosos S02E01** | Funciona en servidor | HD-Olimpo, prepare 1,5 s, stream_ready rápido |
| **Tierra de mafiosos S02E02** | Funciona en servidor | HD-Olimpo, prepare 3,91 s, PlaybackInfo 4,13 s, Jellyfin stream 206 en 1,86 s |
| **Tierra de mafiosos S02E03** (iOS) | Primer fotograma + spinner + «emisión en vivo» | HLS `live.m3u8` / playlist `EVENT` por `RunTimeTicks` nulo |

Evidencia del último caso:

```text
# Master playlist de Jellyfin
#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=200000000,...,RESOLUTION=1920x1080
live.m3u8?...

# Variante
#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:10
#EXT-X-MEDIA-SEQUENCE:0
#EXT-X-PLAYLIST-TYPE:EVENT
#EXTINF:10.427089,
hls/....ts
```

El transcode de FFmpeg estaba generando segmentos correctamente; el problema
no era falta de datos, sino que Jellyfin lo estaba tratando como directo.

## Causa raíz

`EncodingJobInfo.IsSegmentedLiveStream` es verdadero si el trabajo HLS no es
progresivo y `RunTimeTicks` es nulo. En ese caso `DynamicHlsHelper` usa
`live.m3u8` y la playlist se sirve como `EVENT`; el cliente iOS lo interpreta
como emisión en vivo.

En la respuesta de `PlaybackInfo` sí vimos:

```json
{
  "RunTimeTicks": 27110650000,
  "IsInfiniteStream": false,
  "VideoType": "VideoFile"
}
```

Pero el estado que usa el controlador HLS se construye de nuevo desde el ítem
y su `MediaSource`. El plugin actual:

- asigna `item.RunTimeTicks` en `SaveStreams`;
- no llama a `UpdateToRepositoryAsync`, así que la duración no queda persistida;
- en `Decorate` asigna `source.RunTimeTicks`, pero eso solo afecta a la respuesta
  de `PlaybackInfo`, no al ítem que luego lee el HLS.

Resultado: `state.RunTimeTicks` nulo → `live.m3u8` → UI de emisión en vivo.

## Estado actual del servidor

- Plugin nuevo cargado con `NativeMode = true`.
- `SupportsDirectPlay = false` en modo nativo: el cliente ya no intenta abrir
  `127.0.0.1:8773` directamente.
- Jellyfin entrega `TranscodingUrl` propio (`/videos/{id}/master.m3u8`).
- El transcode se ejecuta y genera segmentos; el problema que queda es que el
  HLS es `live.m3u8` en vez de `main.m3u8`.
- Medusa sigue por el camino de compatibilidad.

## Fix pendiente

Persistir la duración del ítem antes de que Jellyfin cree el estado HLS.

Opciones, en orden de preferencia:

1. En `SaveStreams`, después de `item.RunTimeTicks = ticks`, llamar a
   `await item.UpdateToRepositoryAsync(ItemUpdateType.MetadataEdit, cancellationToken)`
   (o la API equivalente de `ILibraryManager` en Jellyfin 12.4).
2. Si el ítem ya trae `RunTimeTicks` de TMDB/NFO, no sobrescribirlo con un valor
   menor; usar el mayor fiable.
3. Como refuerzo, decorar también `GetMediaSource`/`GetStaticMediaSources` para
   que el `MediaSourceInfo` que llega al HLS tenga `RunTimeTicks` no nulo.
4. Verificar después que el master playlist devuelve `main.m3u8`, la variante
   es `VOD` y el cliente iOS muestra barra de progreso normal.

No se ha implementado todavía el fix de persistencia: el usuario pidió parar y
documentar.

## Plan de prueba tras el fix

1. Elegir un `.strm` frío con fuente viva y tracker activo.
2. `PlaybackInfo` con cabecera de iOS y perfil de vídeo.
3. Comprobar en la respuesta:
   - `RunTimeTicks` no nulo;
   - `SupportsDirectPlay = false`;
   - `TranscodingUrl` presente.
4. Pedir el master playlist y comprobar:
   - variante `main.m3u8`;
   - `#EXT-X-PLAYLIST-TYPE:VOD`;
   - `#EXT-X-ENDLIST` al terminar.
5. Reproducir en Jellyfin iOS: sin indicador de directo, con barra de progreso.
6. Repetir con Medusa para confirmar que no hay regresión.
7. Repetir con un título frío y con otro ya descargado.

## Rollback

- Plugin: `NativeMode = false` o restaurar el DLL anterior.
- Backups en `/home/dietpi/backups-20261124/` y en
  `Medusa_1.1.4.0/Jellyfin.Plugin.Catalogo.dll.bak-*`.
- TOD: `git checkout` de `bf01478`/`78e7bed` y reiniciar `tod.service`.
- No hay migraciones de base de datos.

## Notas

- La API key usada para las pruebas no se guarda en este documento.
- Los fallos por «sin fuente» son de disponibilidad de trackers, no del plugin:
  TOD solo puede servir lo que el tracker y qBittorrent permiten.
- `Good Burger 2`, `Battle Over Britain` y `El soldado desconocido` no son
  buenos casos de prueba: sus fuentes estaban muertas o bloqueadas.
- Los casos válidos verificados son `Tierra de mafiosos` S02E01/S02E02
  (HD-Olimpo) y, como frío pendiente, S02E03/S02E04.
