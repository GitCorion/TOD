import { api } from '../api.js';
import { esc, fmtInt } from '../format.js';
import { updateQuery } from '../router.js';
import { $, badge, empty, icon, poster, setHeader, status } from '../ui.js';

function partState(part) {
  if (!part.in_catalog) return status('No está en catálogo', 'muted');
  if (part.downloaded) return status('Descargado', 'ok');
  if (part.published) return status('En biblioteca', 'ok');
  if (part.library_added && part.monitored && part.available === false) return status('Pendiente de estreno', 'muted');
  if (part.library_added && part.monitored) return `${status('En biblioteca, sin enlace', 'warn')} ${status('En seguimiento', 'info')}`;
  if (part.library_added) return status('En biblioteca, sin enlace', 'warn');
  if (part.publication === 'pendiente') return status('Sin verificar', 'warn');
  return status('Sin fuente', 'muted');
}

export async function renderCollection({ params, signal }) {
  const collectionId = Number(params[0] || 0);
  if (!collectionId) { location.hash = '#/catalog'; return; }
  const data = await api(`/api/collections/${collectionId}`, { signal });
  const parts = data.parts || [];
  const inCatalog = parts.filter((part) => part.in_catalog).length;
  const inLibrary = parts.filter((part) => part.library_added).length;
  const noSource = parts.filter((part) => part.in_catalog && !part.library_added && part.publication === 'sin_fuente').length;
  const withoutLink = parts.filter((part) => part.library_added && !part.published).length;
  setHeader('Saga', 'Catálogo');
  document.title = `${data.name || 'Saga'}, TOD`;

  const cards = parts.map((part) => {
    const href = part.asset_id ? `#/title/${part.asset_id}` : '';
    let action;
    if (!part.in_catalog) {
      action = `<button class="btn btn-sm btn-primary" data-action="add-movie" data-tmdb="${part.tmdb}" data-title="${esc(part.title)}">${icon('plus', 'sm')} Añadir</button>`;
    } else if (part.published) {
      action = `<a class="btn btn-sm btn-ghost" href="${href}">Abrir</a>`;
    } else if (part.library_added) {
      action = `<button class="btn btn-sm" data-action="manual-search" data-id="${part.asset_id}" data-title="${esc(part.title)}">${icon('search', 'sm')} Buscar fuentes</button>
                <a class="btn btn-sm btn-ghost" href="${href}">Abrir</a>`;
    } else {
      action = `<button class="btn btn-sm btn-primary" data-action="library-title" data-id="${part.asset_id}" data-key="${esc(part.key)}" data-title="${esc(part.title)}" data-releases="${part.publication === 'pendiente' ? 1 : 0}">${icon('plus', 'sm')} Añadir a la biblioteca</button>
                <a class="btn btn-sm btn-ghost" href="${href}">Abrir</a>`;
    }
    return `<article class="media-card" ${href ? `data-href="${href}" tabindex="0" role="link"` : ''}>
      <div class="poster">${poster(part.poster_path, part.title)}</div>
      <div class="poster-progress ${part.library_added ? 'done' : part.in_catalog ? 'partial' : 'none'}"><i style="width:${part.library_added ? 100 : 0}%"></i></div>
      <div class="meta">
        <strong>${esc(part.title || '—')}</strong>
        <small>${part.year || '—'}</small>
        ${partState(part)}
        <div class="row" style="margin-top:auto;padding-top:4px"><span style="margin-left:auto">${action}</span></div>
      </div>
    </article>`;
  }).join('');

  $('#view').innerHTML = `
    <div class="breadcrumb"><a href="#/catalog">Catálogo</a>${icon('chevron')}<span>${esc(data.name || 'Colección')}</span></div>

    <div class="detail-hero">
      <div class="poster">${poster(data.poster_path, data.name)}</div>
      <div class="info">
        <h2>${esc(data.name || 'Colección')}</h2>
        <div class="row">
          ${badge('saga', 'mono')}
          ${status(`${fmtInt(parts.length)} películas`, 'muted')}
          ${status(`${fmtInt(inCatalog)} en catálogo`, 'info')}
          ${status(`${fmtInt(inLibrary)} en biblioteca`, 'ok')}
          ${withoutLink ? status(`${fmtInt(withoutLink)} sin enlace`, 'warn') : ''}
          ${noSource ? status(`${fmtInt(noSource)} sin fuente`, 'warn') : ''}
        </div>
        ${data.overview ? `<p class="overview">${esc(data.overview)}</p>` : ''}
        <div class="detail-actions">
          <button class="btn btn-primary" data-action="collection-add" data-id="${data.id}" data-mode="library">${icon('plus')} Añadir toda la colección a la biblioteca</button>
          <button class="btn" data-action="collection-add" data-id="${data.id}" data-mode="catalog">${icon('plus')} Añadir al catálogo</button>
        </div>
      </div>
    </div>

    ${parts.length
      ? `<div class="poster-grid section">${cards}</div>`
      : `<div class="panel section">${empty('Colección vacía')}</div>`}
  `;
}
