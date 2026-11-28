# Diseño de TOD

TOD es Radarr y Sonarr juntos, pero para torrents bajo demanda.

La inspiración es la forma en que ellos muestran y catalogan el contenido: una
biblioteca que se recorre de un vistazo, cada título en su sitio, su estado y su
calidad a la vista. Lo que allí resulta familiar aquí también lo es; lo que
cambia es que las fuentes aparecen y desaparecen bajo demanda, y la interfaz lo
cuenta.

La interfaz se parece a ellos en lo esencial. El catálogo es el centro; la
información útil vive junto a cada título; los detalles están a un paso cuando
se necesitan. Quien conozca esas herramientas se reconoce enseguida.

El tono es sobrio y práctico, en español de España. Las palabras claras, los
estados comprensibles, la acción a mano.

Esta es la dirección y también el sistema. Los valores concretos están aquí; el
detalle de cada pantalla se decide dentro de estas reglas.

## Principios

1. **Una pregunta por pantalla.** Cada página tiene un foco y una acción
   primaria. Si hay dos acciones principales, una sobra.
2. **El estado manda.** Un título tiene un estado visible. Origen, pertenencia,
   descarga y calidad son datos secundarios, no píldoras del mismo nivel.
3. **Los números cuadran.** El mismo concepto tiene el mismo valor en el menú,
   la tarjeta y el filtro. Si no coincide, es un fallo, no un matiz.
4. **Nada del motor en pantalla.** Ni claves (`movie:tmdb:…`), ni eventos
   (`web.seed_settings`), ni nombres de fichero como título.
5. **La portada es el material.** El póster ocupa el sitio que le corresponde;
   la interfaz se organiza alrededor del título, no alrededor de contadores.
6. **Densidad con aire.** Es una herramienta compacta, no un panel apretado:
   jerarquía clara, agrupación por proximidad y espacio entre secciones.
7. **La acción a mano.** La acción principal de cada pantalla se ve sin buscarla
   y el resto queda a un paso, no escondido.
8. **Honestidad de datos.** Una fuente viva se muestra; una fuente que no existe
   se muestra como `0`, no se omite. Un estado no se pinta verde si el título no
   se puede reproducir.

## El armazón

Una sola aplicación con barra lateral fija (colapsable en escritorio) y contenido ancho, inspirada en la claridad directa de Trailarr.

| Pieza | Medida | Nota |
|---|---|---|
| Barra lateral | 232 px (64 px colapsada) | Fija; colapsable en escritorio (`tod.sidebarCollapsed`) y fuera de pantalla en móvil con `.js` |
| Barra superior | 52 px | Título de página, buscador global (se oculta en Películas/Series y Alta) y **Añadir** |
| Pestañas de sección | 38 px (36 en móvil) | Pegadas bajo la barra superior solo en secciones con sub-vistas |
| Contenido | máx. 1680 px | `padding: 20px 24px 64px` |
| Navegación móvil | 58 px | Inicio, Películas, Series, Añadir, Más |

- **Barra lateral:** Inicio, Películas, Series, Añadir, Descargas, Tareas, Eventos, Configuración. Películas y Series llevan sus totales; Descargas y Tareas muestran elementos activos; Configuración muestra los avisos. Los iconos siempre van con etiqueta cuando está expandida.
- **Pestañas de sección:** Descargas (Cola, Faltantes, Calendario, Siembra, Blocklist), Eventos (Eventos, Registros, Auditoría) y Configuración (Configuración, Estado del sistema). Todas las rutas clásicas (`#/catalog`, `#/system`, `#/history`) siguen funcionando como alias.
- **Móvil:** la barra lateral se abre con el botón de menú; scrim y cierre con
  `Esc`; la navegación inferior es la principal. La cabecera de la biblioteca no
  se queda fija en móvil: ocuparía media pantalla.
- **Cabeceras fijas:** la biblioteca (escritorio) se pega bajo las pestañas para
  no perder búsqueda, estado y filtros al recorrer el catálogo. La ficha mantiene
  fijo el panel de detalles mientras se lee el resumen.

## Pantallas

### Biblioteca

Es el centro del producto. La rejilla de carteles es la vista por defecto; la
tabla queda para trabajo fino.

**Cabecera (fija en escritorio).**

1. Buscador de la biblioteca: título, serie o año.
2. **Tipo:** Todos, Películas, Series.
3. **Vista:** carteles o tabla.
4. **Estado:** Todos, Disponible, Pendiente, Sin fuente. En móvil, una
   rejilla 2×2 con los cuatro estados y sus contadores.
5. **Orden:** popularidad, título, año, añadido.
6. Filtros activos como chips con su aspa: Descargado, Radarr, Solo incompletos…
7. `X de Y títulos` cuando hay filtros; `X títulos` cuando no.
8. **Más filtros:** descargas, procedencia y solo incompletos en un panel.

**Cartel.**

- Póster 2:3, `minmax(146px, 1fr)`, 16 px de hueco. En móvil, 132 px.
- Estado abajo a la izquierda, sobre un velo oscuro: texto en el color del
  estado y un punto. La palabra siempre explica el estado; nunca solo color.
- Fuentes vivas arriba a la derecha: `12 fuentes`, `0 fuentes`. El cero se pinta
  en ámbar; nunca se oculta.
- Rail de cobertura al pie **solo en series**: episodios publicados sobre el
  total. En películas no aporta y no se pinta.
- Casilla de selección: oculta en reposo con ratón, visible al pasar, al enfocar
  o al seleccionar; en táctil se queda visible. Zona de 44 × 44 px.
- Debajo: título a una o dos líneas, año y tipo. Sin píldoras de estado
  adicionales.

**Tabla.**

Columnas: Título, Estado, Fuentes, Procedencia, Añadido. La fila entera abre la
ficha; la casilla selecciona. En móvil se apila con `data-label`; las columnas
vacías se ocultan.

**Reglas de la pantalla.**

- La vista por defecto es carteles. La tabla es una elección explícita.
- El estado de la portada describe **reproducción**: verde si hay fuente viva o
  fichero; ámbar `Sin fuente` si el `.strm` existe pero no hay nada que
  reproducir (se retirará al reconciliar); azul `Pendiente` si hay fuente pero no `.strm`; gris
  `Sin fuente` si no hay ni lo uno ni lo otro.
- La fila de filtros describe **publicación**, que es el eje con el que se
  trabaja: Disponible, Pendiente, Sin fuente. El estado compuesto de la portada
  reconcilia ambos sin mentir. Un título publicado sin fuente sigue contando en
  «Disponible» porque el `.strm` existe; la portada lo pinta como `Sin fuente`
  en ámbar para que el verde no mienta.
- **Solo cuenta lo emitido.** Un episodio sin estrenar se muestra
  `Sin estrenar` y no suma como fuente ausente, sin añadir ni incompleto. Una
  serie con todos sus episodios emitidos disponibles está `Disponible` aunque
  tenga temporadas futuras; estas se resumen como `N sin estrenar` en la ficha y
  en el título accesible del cartel, no en la línea de metadatos.
- **Los especiales no cuentan.** La temporada 0 tiene su propio chip y su
  contador, sin punto de aviso y sin efecto en la disponibilidad ni en los
  faltantes de la serie. Se pueden añadir aparte.
- El contador del menú es el total del catálogo agrupado; el de la línea de
  resultados es el del filtro actual (`753 de 3329`).
- Las facetas se calculan después de la búsqueda y del tipo, y antes del estado:
  elegir “Sin fuente” no pone a cero los demás estados.
- 60 títulos por página. La paginación está al pie, con “Anterior / Siguiente” y
  el número de página.

### Ficha

La página de la película o del episodio.

- **Migas:** Biblioteca › serie › episodio.
- **Héroe:** fondo difuminado con `backdrop_path` de TMDB (respaldo de póster) con degradado legible en ambos temas, póster (148 px, hasta 196 en pantallas grandes), título y año, sinopsis a 65ch, colección si existe y fila de acciones. El estado y la pertenencia van en la misma fila que el título: `Disponible`, `En biblioteca`, `En Radarr/Sonarr`, `Descargada`.
- **Franja de datos reales:** releases vivos, releases totales, ficheros, calidad, compromisos y eventos. Es la misma franja en la ficha y en la serie.
- **Pestañas:** Resumen y Avanzado (`Releases`, `Ficheros y siembra`, `Historial`). Las rutas con `?tab=fuentes|ficheros|historial` siguen abriendo directamente la sub-sección correspondiente.
- **Resumen:** los tres mejores releases (calidad, seeds, salud, tamaño) y los
  dos primeros ficheros, con “Ver todos”. Es una vista previa, no
  una copia de las tablas.
- **Panel de detalles:** a la derecha, fijo en escritorio. Calidad, audio,
  procedencia, ubicación y releases vivos. Solo acompaña a Resumen:
  en Releases, Ficheros e Historial la tabla ocupa todo el ancho.
- **Releases:** tabla a ancho completo. En escritorio, columnas fijas, cifras sin
  salto de línea y acciones visibles (Ficha, Usar por defecto, Verificar, Preparar/Forzar, Blocklist). La
  privacidad es una etiqueta en la línea del tracker. En móvil, cada release es
  una ficha: título, tracker y privacidad, rejilla 2×2 de calidad, tamaño, seeds
  y salud, y las acciones debajo. La lista larga tiene scroll interno (70dvh),
  no alarga la página.
- **Acciones del héroe:** una primaria, el resto fantasma; la destructiva
  separada. En móvil, una columna de ancho completo y “Quitar de biblioteca” en su
  propia fila con una línea fina.

### Serie

- Héroe con póster, título, año, badges, sinopsis y acciones.
- Franja de datos: episodios, en biblioteca, monitorizados, sin fuente, sin
  añadir, especiales.
- Chips de temporada con su contador y un punto de aviso si falta algo.
- Tabla de episodios: título con still, estado, fuentes, progreso y acciones.
  En móvil se apila.

### Alta

- **Buscador primero:** una barra con icono, botón **Buscar** y una ayuda que
  explica que también acepta un enlace, una lista o un `.torrent`.
- `Enter` busca; `Mayús+Enter` añade una línea. El botón cambia entre **Buscar**
  y **Añadir** según lo escrito.
- **Monitorizar (buscar automáticamente si no hay fuente)** y **Subir .torrent** en una fila secundaria.
- **Resultados:** póster, título, año y tipo, con un botón **Añadir** explícito;
  los que ya están muestran **Abrir**.
- **Últimas entradas:** panel lateral fijo en escritorio; debajo de los
  resultados en móvil.
- Estado inicial, sin resultados y error tienen su propio mensaje y su acción.

### Inicio, Actividad y Sistema

Son pantallas de operación; comparten el sistema y no se reinventan.

- **Inicio:** avisos, cifras de la biblioteca, últimas incorporaciones, alta rápida,
  cola de descargas, Radarr/Sonarr y últimas tareas.
- **Actividad:** cola, tareas, historial, siembra y blocklist; barra de progreso y acciones por
  fila.
- **Sistema:** Estado (diagnóstico, servicios, discos, auditoría) y Configuración
  (general y rutas, integraciones e indexers, perfiles y custom formats, gestión de medios `.strm`, etiquetas y listas, trackers y siembra, tareas programadas) con pruebas de conexión y el motivo concreto de cada fallo.

## Color

Los tokens están en `catalogo/web_static/styles.css`. Ningún color literal fuera
del bloque de tokens.

| Token | Oscuro | Claro | Uso |
|---|---|---|---|
| `--paper` | `#0f1114` | `#eef0f3` | Fondo |
| `--paper-soft` | `#16191d` | `#ffffff` | Paneles |
| `--paper-deep` | `#1e2226` | `#e7eaee` | Cabeceras, campos, hover |
| `--ink` | `#e8ebee` | `#191c20` | Texto principal |
| `--graphite` | `#9aa4ad` | `#545e68` | Texto secundario |
| `--faint` | `#8b959f` | `#5f6872` | Metadatos (≥ 4,5:1) |
| `--rule` | `#272d33` | `#d7dce2` | Bordes |
| `--rule-dark` | `#39424a` | `#b9c1c9` | Bordes fuertes |
| `--field-line` | `#6b7680` | `#7d8794` | Borde de campos (≥ 3:1) |
| `--accent` | `#eab64b` | `#9a6408` | Acción y foco |
| `--accent-hover` | `#f6c65f` | `#c9861c` | Hover de la acción |
| `--accent-fill` | `#eab64b` | `#e0a63a` | Relleno del botón primario |
| `--accent-ink` | `#17120a` | `#1a1405` | Texto sobre ámbar |
| `--green` | `#62bd84` | `#2a7449` | Disponible, correcto |
| `--amber` | `#e08a45` | `#9a5a12` | Aviso, fuente ausente |
| `--red` | `#e2707a` | `#b33a44` | Error, destructivo |
| `--blue` | `#63aee3` | `#2f6f9e` | Información, pendiente |

- **El ámbar es acción y foco**, no un estado de éxito.
- Verde, ámbar y rojo solo con texto que explique el estado. Nunca color solo.
- **Sobre póster** se usa el mismo velo oscuro en ambos temas y una paleta
  propia, más clara, `--on-media-*`; el verde del tema claro no se lee sobre una
  foto oscura.
- Los estados usan sus variantes suaves (`--green-soft`, `--amber-soft`…), no
  rellenos planos.
- **Contraste:** cuerpo 4,5:1; texto grande, iconos, foco y bordes de campo 3:1.
- El tema claro se deriva del oscuro; no es una inversión. `theme.js` guarda la
  preferencia y el atributo `data-theme` vive en `html`.
- La selección de texto usa el ámbar con su tinta.

## Tipografía

IBM Plex Sans, servida en local (400, 500, 600). Monoespaciada solo para datos
operativos y cifras que se comparan.

| Rol | Tamaño | Peso |
|---|---|---|
| Cuerpo | 13,5 px | 400 |
| Metadatos y ayudas | 11,5–12 px | 400 |
| Etiqueta de campo | 11,5 px | 500 |
| Título de panel | 15 px | 600 |
| Título de página | 21 px | 600 |
| Héroe de ficha | 26 px | 600 |
| Título de portada | 13 px | 500 |
| Estado en portada | 11 px | 500 |

- **Suelo de 11 px** para cualquier texto funcional. Nada de 9,5 px.
- **Frase normal, sin versales ni tracking.** Etiquetas, pestañas, cabeceras de
  tabla, chips y navegación van en la fuente del cuerpo, un paso más pequeña.
- Sin *kickers* de una palabra encima del `h1` (`CONTENIDO`, `SISTEMA`).
- Cifras tabulares en tablas, contadores, seeds y tiempos.
- Línea de cuerpo 1,45; sinopsis 65ch; medidas de texto 45–75ch.
- En móvil, los campos usan 16 px para evitar el zoom de iOS.
- Sin cursivas dentro de un título; sin una palabra de otro color en un título.
- Comillas españolas, elipsis real y coma entre metadatos; nada de puntos medios
  ni de flechas pegadas al texto.

## Espaciado, radios y superficies

- **Escala de 4 px:** 4, 8, 12, 16, 24, 32, 40, 64. Se admiten ajustes ópticos de
  1–2 px.
- **Controles de 32 px** de alto; en móvil, 36 px. Objetivo táctil de 44 px en
  selección y acciones de portada.
- **Radios:** 4 px (`--radius`), 3 px para piezas pequeñas (`--radius-sm`), 6 px
  para paneles (`--radius-lg`). Concéntricos: el interior = exterior − padding.
- **Un solo nivel de tarjeta.** No se anidan tarjetas: se usan divisores, filas,
  la franja de datos y espacio. Los paneles, las tablas y las franjas comparten
  el mismo borde fino a propósito.
- **Superficies:** papel, papel suave, papel profundo. Se sube un escalón, no se
  añaden sombras.
- **Sombras** solo en modales, paleta, toasts y el popover de filtros.
- **Reglas finas** de 1 px; sin bordes de acento gruesos en un lado de una
  tarjeta.
- Las tablas usan `data-label` para apilarse en móvil; las cabeceras no se quedan
  fijas en la página (competirían con la cabecera de la biblioteca).
- Las listas largas (fuentes de una ficha) tienen scroll interno antes que
  alargar la página.

## Estados y datos

Un título tiene un estado principal, el que explica si se puede ver.

| Estado | Cuándo | Tono |
|---|---|---|
| **Disponible** | Publicado y reproducible: fuente viva o fichero local | verde |
| **Pendiente** | Hay fuente o fichero, pero no `.strm` | azul |
| **En seguimiento** | TOD buscará fuente; todavía no hay nada | azul |
| **Sin fuente** | No hay nada reproducible | gris |
| **Sin estrenar** | El episodio aún no se ha emitido | gris |
| **Descargando** | Descarga activa; con porcentaje si lo hay | azul |
| **Descarga pausada** | Descarga detenida | ámbar |
| **Error de descarga** | qBittorrent informó de un error | rojo |

- **Publicación y reproducción son dos hechos.** El filtro de la biblioteca usa
  la publicación; la portada usa la reproducción. Cuando no coinciden, el estado
  cover lo dice: un `.strm` sin fuente ni fichero se muestra `Sin fuente` en
  ámbar, no como un estado más. Es una inconsistencia transitoria: la
  reconciliación retira el `.strm`, o se quita desde la ficha. Si el `.strm`
  existe porque Radarr/Sonarr tiene la descarga en cola, el *arr ya tiene una
  fuente y la ficha y el cartel muestran `Descargando`, que es el hecho real.
  El contador `0 fuentes` sigue contando solo releases verificados por TOD, no
  la cola del *arr; por eso el estado es la lectura principal.
- **Fuentes vivas:** el número siempre se imprime, incluido `0 fuentes`. Un
  título con `0` y sin fichero no se pinta verde.
- **Calidad:** la biblioteca no inventa una calidad por título; muestra fuentes
  vivas, que es la disponibilidad real. La calidad concreta vive en la ficha,
  por versión.
- **Contadores:** las facetas se calculan tras la búsqueda y el tipo, antes del
  estado. El total del menú es el catálogo agrupado; el de la línea de resultados
  es el filtro actual. Un concepto, un número.
- **Origen, pertenencia, descarga y calidad** son datos secundarios, no estados
  principales.
- **Cero, vacío y error** tienen su mensaje: qué falta, por qué importa y la
  acción que lo resuelve. Una región que carga tiene su esqueleto con la forma de
  lo que viene.

## Componentes

| Pieza | Clase | Uso |
|---|---|---|
| Panel | `.panel`, `.panel-head`, `.panel-body` | Una sola densidad; cabecera con título y acción |
| Franja de datos | `.record-line` | Cifras reales de una ficha o una serie |
| Estado | `.status` | Punto y texto; tono `ok`, `info`, `warn`, `bad`, `muted` |
| Etiqueta neutra | `.badge` | Tipo, año, tracker, privacidad |
| Segmentado | `.seg`, `.seg-item`, `.seg-group`, `.seg-label` | Tipo, estado, filtros; una sola receta de selección |
| Chip | `.chip`, `.chip-clear` | Filtros activos con su aspa |
| Rejilla | `.poster-grid` | Carteles; `minmax(146px, 1fr)` |
| Cartel | `.catalog-card`, `.catalog-state`, `.catalog-sources`, `.catalog-rail` | Portada, estado, fuentes y cobertura |
| Tabla | `.table-wrap`, `.table--stack`, `.catalog-table` | Trabajo fino; se apila en móvil |
| Ficha | `.detail-hero`, `.detail-card`, `.title-layout`, `.title-aside`, `.title-preview` | Héroe, franja, pestañas y detalles |
| Alta | `.add-search`, `.add-result`, `.add-aside` | Buscador, resultados y últimas entradas |
| Vacío | `.empty` | Qué está vacío, por qué importa y una acción |
| Aviso | `.notice` | Severidad, motivo y enlace al ajuste |
| Modal | `.modal`, `.modal-card` | Foco atrapado, `Esc` cierra, foco devuelto |
| Botones | `.btn`, `.btn-primary`, `.btn-ghost`, `.btn-danger`, `.btn-sm`, `.btn-icon` | Una sola jerarquía por pantalla |

- **Estado:** punto + texto, 12 px. No se usa `badge` para estados.
- **Botón primario:** uno por pantalla. El resto, fantasma o icono.
- **Acciones destructivas:** texto rojo, separadas; nunca el mismo peso que las
  demás.
- **Formularios:** etiqueta arriba, campo de 32 px, error debajo, ayuda en gris.
  Foco con `outline` de 2 px, no solo borde.

## Accesibilidad

- Contraste AA (4,5:1) en texto normal; foco visible en todo interactivo.
- `:focus-visible` con anillo de 2 px y desplazamiento; nunca se anima.
- Navegación completa por teclado; `Ctrl K` para la paleta; `Esc` cierra modales
  y menús; `Enter`/`Espacio` abren una portada.
- `skip-link`, landmarks, `aria-current`, `aria-pressed` en los conmutadores de vista,
  `aria-live` en resultados, toasts y selección.
- Las casillas de portada se ocultan en reposo con ratón, pero siguen siendo
  enfocables; en táctil se muestran y su zona es de 44 px.
- No comunicar estado solo por color: siempre hay palabra.
- `prefers-reduced-motion`: todas las transiciones y animaciones se reducen.
- Campos a 16 px en móvil para no provocar zoom.
- Las tablas se apilan con etiqueta visible; las columnas vacías se ocultan.

## Copy

- Español de España, frases cortas, sin mayúsculas decorativas.
- **Estados:** «Disponible», «Pendiente», «En seguimiento», «Sin fuente», «Sin estrenar».
- **Vocabulario:** «Versiones» para las entradas de una fuente; «Fuentes» para el
  eje y el contador; «Seeds» para las semillas de un torrent; «Siembra» para la
  política; «Indexers» solo en Configuración; «Seguimiento» para lo que TOD
  buscará.
- Un botón dice lo que hace: «Buscar fuentes», «Buscar y añadir», «Quitar de
  TOD». Jellyfin solo se nombra en la configuración de la integración y en los
  ficheros que lee. Nunca «Aceptar» para una acción destructiva.
- Los errores dicen qué pasó, por qué y qué hacer después.
- Los vacíos dicen qué falta y cuál es la siguiente acción.
- Sin puntos medios entre metadatos: coma. Sin flechas pegadas al texto. Sin
  emoji como icono. Sin signos de exclamación.
- Las cifras no se inventan. Un dato que no existe se muestra como `—` o `0`,
  nunca como una estimación.

## Lo que no se hace

- Rejillas de tarjetas idénticas sin jerarquía.
- Degradados, cristal, sombras grandes o animaciones decorativas.
- Tarjetas dentro de tarjetas.
- Etiquetas en versales, con tracking o por debajo de 11 px.
- Iconos sin etiqueta en la navegación principal.
- Emoji como icono.
- Mostrar claves, rutas internas o nombres de fichero como títulos.
- Pintar un estado solo con color, o un verde que no se puede reproducir.
- Ocultar un `0` para que la portada parezca mejor.
- `transition: all` o animar `width`, `height`, `top` o `left`.
- Scroll horizontal en cualquier ancho de 320 a 1920.
- Un buscador duplicado en la misma pantalla: cuando la pantalla tiene el suyo
  (Biblioteca, Alta), el buscador global se oculta.

## Mapa técnico

| Archivo | Qué contiene |
|---|---|
| `catalogo/web_static/styles.css` | Tokens, armazón, componentes y los bloques de biblioteca, ficha y alta |
| `catalogo/web_static/index.html` | Armazón: barra lateral, barra superior, pestañas, navegación móvil |
| `catalogo/web_static/theme.js` | Tema guardado y clase `.js` para el estado de la barra lateral |
| `catalogo/web_static/js/ui.js` | `status`, `badge`, `poster`, `empty`, modales, toasts y utilidades |
| `catalogo/web_static/js/views/catalog.js` | Biblioteca: filtros, portadas, tabla y paginación |
| `catalogo/web_static/js/views/title.js` | Ficha: héroe, franja, pestañas, fuentes y detalles |
| `catalogo/web_static/js/views/series.js` | Serie: héroe, franja, temporadas y episodios |
| `catalogo/web_static/js/views/add.js` | Alta: buscador, resultados, lotes y últimas entradas |
| `catalogo/web_static/js/app.js` | Eventos globales, selección, paleta y tareas |
| `catalogo/ondemand.py` | Catálogo agrupado, facetas y estado de cada título |
| `tests/` | Pruebas del motor y de la API |
| `docs/GLOSARIO.md` | Vocabulario de la interfaz |

## Al añadir una pantalla

1. Lee este documento y los tokens. No inventes colores ni tipografías.
2. Define el trabajo de la pantalla y su acción primaria. Una de cada.
3. Decide el estado principal del objeto y cómo se lee sin color.
4. Usa el armazón: barra lateral, pestañas de sección si corresponde y el ancho
   de contenido.
5. Cubre lleno, vacío, carga y error en cada región que trae datos.
6. Controles de 32 px, foco visible, teclado, 44 px en táctil.
7. Comprueba oscuro y claro, 390, 768, 1280 y 1920, y `prefers-reduced-motion`.
8. Pasa `python -m pytest -q`. Si tocas la interfaz, revisa contraste y estados.
9. Si cambias una regla del sistema, actualiza este archivo.

## Estado actual

- **Biblioteca:** rejilla de carteles por defecto, cabecera fija con búsqueda,
  tipo, estado, orden y filtros; tabla; paginación; estado honesto con fuentes.
- **Ficha:** héroe, franja de datos reales, pestañas, panel de detalles y tabla
  de fuentes con scroll interno.
- **Serie:** héroe, franja, temporadas y episodios.
- **Alta:** buscador primero, resultados y últimas entradas.
- **Inicio, Actividad y Sistema:** conservan su estructura operativa y comparten
  el sistema.

Lo siguiente, si se retoma: el alta puede ganar una búsqueda mientras se escribe
y la ficha un fondo de TMDB si se guarda `backdrop_path`; ninguna de las dos es
necesaria para que el conjunto esté cerrado.
