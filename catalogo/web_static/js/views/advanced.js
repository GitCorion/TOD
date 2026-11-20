import { $, icon, setHeader } from '../ui.js';

export function renderAdvanced() {
  setHeader('Avanzado', 'Sistema');
  $('#view').innerHTML = `
    <div class="grid grid-2">
      <a class="panel pad" href="#/technical" style="color:inherit">
        <strong>Catálogo técnico</strong>
        <p class="muted tiny" style="margin:4px 0 0">Fuentes, releases, salud y disponibilidad interna.</p>
        <span class="row tiny" style="margin-top:10px">Abrir ${icon('chevron', 'sm')}</span>
      </a>
      <a class="panel pad" href="#/jobs" style="color:inherit">
        <strong>Tareas</strong>
        <p class="muted tiny" style="margin:4px 0 0">Captura, mantenimiento y trabajos en segundo plano.</p>
        <span class="row tiny" style="margin-top:10px">Abrir ${icon('chevron', 'sm')}</span>
      </a>
      <a class="panel pad" href="#/seeds" style="color:inherit">
        <strong>Siembra</strong>
        <p class="muted tiny" style="margin:4px 0 0">Compromisos, ratios y liberación de espacio.</p>
        <span class="row tiny" style="margin-top:10px">Abrir ${icon('chevron', 'sm')}</span>
      </a>
      <a class="panel pad" href="#/system" style="color:inherit">
        <strong>Estado del sistema</strong>
        <p class="muted tiny" style="margin:4px 0 0">Servicios, discos, trackers y auditoría.</p>
        <span class="row tiny" style="margin-top:10px">Abrir ${icon('chevron', 'sm')}</span>
      </a>
    </div>
    <div class="panel pad section">
      <strong class="tiny">Radarr y Sonarr en solo lectura</strong>
    </div>
  `;
}
