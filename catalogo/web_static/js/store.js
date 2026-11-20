export const state = {
  session: { auth_required: false, authenticated: true },
  view: '',
  params: [],
  query: new URLSearchParams(),
  signal: null,
  controller: null,
  selection: new Set(),
  torrentSelection: new Set(),
  paletteItems: [],
  paletteIndex: 0,
  activity: null,
  lastJobId: null,
  tmdb: { kind: 'movie', query: '', year: '', results: [] },
  detailTab: 'resumen',
  season: null,
  filtersOpen: false,
  downloadsExpanded: new Set(),
  filters: { q: '', kind: '', publication: 'publicado', download: '', sort: 'popularity', view: 'grid', page: 1 },
};

export function beginRequest() {
  if (state.controller) state.controller.abort();
  state.controller = new AbortController();
  state.signal = state.controller.signal;
  return state.signal;
}

export function clearSelection() {
  state.selection.clear();
}

export function toggleSelection(key) {
  if (state.selection.has(key)) state.selection.delete(key);
  else state.selection.add(key);
  return state.selection.has(key);
}
