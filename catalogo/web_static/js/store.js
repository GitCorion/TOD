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
  settingsDirty: false,
  downloadsExpanded: new Set(),
  filters: { q: '', kind: '', publication: '', download: '', sort: 'popularity', view: 'grid', page: 1 },
  metaCache: new Map(),
};

export async function cachedApi(key, fetcher, ttlMs = 30000) {
  const hit = state.metaCache.get(key);
  if (hit && Date.now() - hit.ts < ttlMs) return hit.data;
  const data = await fetcher();
  state.metaCache.set(key, { ts: Date.now(), data });
  return data;
}

export function invalidateMetaCache(prefix = '') {
  if (!prefix) {
    state.metaCache.clear();
    return;
  }
  for (const k of state.metaCache.keys()) {
    if (k.startsWith(prefix)) state.metaCache.delete(k);
  }
}

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
