# Diseño de TOD

TOD es una superficie **Operate**: el usuario viene a resolver algo (añadir,
revisar, reproducir, diagnosticar), no a admirar la interfaz. La identidad es
una mesa de control: grafito neutro, reglas finas y un único ámbar de lámpara
de proyector. El color de estado no se usa como decoración.

## Principios

1. **Una pregunta por pantalla.** Cada página tiene un foco y una acción
   primaria. Si hay dos acciones principales, una sobra.
2. **El estado manda.** Un título tiene un solo estado visible. Origen,
   pertenencia, descarga y calidad son datos secundarios, no píldoras del
   mismo nivel.
3. **Los números cuadran.** El mismo concepto tiene el mismo valor en el menú,
   la tarjeta y el filtro. Si no coincide, es un fallo, no un matiz.
4. **Nada del motor en pantalla.** Ni claves (`movie:tmdb:…`), ni eventos
   (`web.seed_settings`), ni nombres de fichero como título. Ver
   [docs/GLOSARIO.md](docs/GLOSARIO.md).
5. **Configuración cambia; Estado lee.** Los interruptores viven en
   Configuración. Estado solo cuenta qué pasa y enlaza al ajuste.
6. **Densidad con aire.** Es una herramienta compacta, no un panel apretado:
   jerarquía clara, agrupación por proximidad y espacio entre secciones.

## Color

Los tokens están en `catalogo/web_static/styles.css`.

| Token | Oscuro | Claro | Uso |
|---|---|---|---|
| `--paper` | `#0f1114` | `#eef0f3` | Fondo |
| `--paper-soft` | `#16191d` | `#ffffff` | Paneles |
| `--paper-deep` | `#1e2226` | `#e7eaee` | Cabeceras, campos |
| `--ink` | `#e8ebee` | `#191c20` | Texto principal |
| `--graphite` | `#9aa4ad` | `#545e68` | Texto secundario |
| `--faint` | `#8b959f` | `#5f6872` | Metadatos (≥ 4,5:1) |
| `--rule` | `#272d33` | `#d7dce2` | Bordes |
| `--accent` | `#eab64b` | `#9a6408` | Acción y foco |
| `--green` | `#62bd84` | `#2a7449` | Correcto / publicado |
| `--amber` | `#e08a45` | `#9a5a12` | Aviso |
| `--red` | `#e2707a` | `#b33a44` | Error / destructivo |
| `--blue` | `#63aee3` | `#2f6f9e` | Información |

Reglas:

- El ámbar es **acción y foco**; no es un estado de éxito.
- Verde, ámbar y rojo solo con texto que explique el estado. Nunca color solo.
- `--faint` no baja de 4,5:1 sobre el fondo en el que se usa.

## Tipografía

IBM Plex Sans, con monoespaciada solo para datos operativos.

| Rol | Tamaño | Peso |
|---|---|---|
| Cuerpo | 13,5 px | 400 |
| Metadatos y ayudas | 12 px | 400 |
| Título de panel | 15 px | 600 |
| Título de página | 21 px | 600 |
| Héroe de ficha | 26 px | 600 |
| Micro-etiqueta funcional | 11 px mínimo | 600 |

- **Suelo de 11 px** para cualquier texto funcional. Nada de 9,5 px.
- Sin *kickers* de una palabra encima del `h1` (`CONTENIDO`, `SISTEMA`).
- Cifras tabulares en tablas, contadores y tiempos.
- En móvil, los campos usan 16 px para evitar el zoom de iOS.

## Espaciado y superficies

- Escala de 4 px: 4, 6, 8, 10, 12, 16, 24.
- Radio: 4 px (`--radius`), 6 px en paneles (`--radius-lg`).
- Un solo nivel de tarjeta. No anidar tarjetas dentro de tarjetas: usar
  divisores, filas y espacio.
- Sin bordes de acento gruesos en un lado de una tarjeta (el «side-tab»).
- Las tablas usan cabecera sticky y `data-label` para apilarse en móvil.

## Estados

Un título tiene exactamente uno de estos estados:

| Estado | Cuándo | Tono |
|---|---|---|
| **En Jellyfin** | Existe su `.strm` | verde |
| **Falta en Jellyfin** | Hay fuente, pero no `.strm` | azul |
| **En seguimiento** | TOD buscará fuente | azul |
| **Sin fuente** | No hay nada reproducible | gris |

El resto son datos secundarios: tipo, año, pertenencia a Radarr/Sonarr,
descarga, origen. No se pintan como estados principales.

## Componentes

- **Panel**: `panel` + `panel-head` + `panel-body`. Una sola densidad.
- **Status**: punto + texto, 12 px. No usar `badge` para estados.
- **Badge**: solo para metadatos neutros (tipo, año, tracker).
- **Tabla**: `table--stack` para móvil; columnas vacías se ocultan.
- **Vacío**: título, una frase y, si existe, una acción.
- **Modal**: foco atrapado, `Esc` cierra, foco devuelto al origen.
- **Aviso**: severidad, motivo, desde cuándo y enlace al ajuste.

## Accesibilidad

- Contraste AA (4,5:1) en texto normal; foco visible en todo interactivo.
- Navegación completa por teclado; `Ctrl K` para la paleta.
- `skip-link`, landmarks, `aria-current`, `aria-live` en toasts y selección.
- `prefers-reduced-motion` respetado.
- No comunicar estado solo por color.

## Copy

- Español de España, frases cortas, sin mayúsculas decorativas.
- «En Jellyfin», «Falta en Jellyfin», «En seguimiento», «Sin fuente».
- «Versiones», no «releases». «Indexers» solo en Configuración.
- Un botón dice lo que hace: «Buscar fuentes», «Añadir a Jellyfin»,
  «Quitar de TOD». Nunca «Aceptar» para una acción destructiva.
- Los errores dicen qué pasó, por qué y qué hacer después.

## Lo que no se hace

- Rejillas de tarjetas idénticas sin jerarquía.
- Degradados, cristal, sombras grandes o animaciones decorativas.
- Iconos sin etiqueta en la navegación principal.
- Texto funcional por debajo de 11 px.
- Mostrar claves, rutas internas o nombres de fichero como títulos.
