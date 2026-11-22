const nf = new Intl.NumberFormat('es-ES');

export function esc(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

export function fmtInt(value) {
  return nf.format(Number(value) || 0);
}

export function fmtBytes(value) {
  let n = Number(value) || 0;
  if (n <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
  let index = 0;
  while (n >= 1024 && index < units.length - 1) { n /= 1024; index += 1; }
  return `${n >= 100 || index === 0 ? n.toFixed(0) : n.toFixed(1)} ${units[index]}`;
}

export function fmtSpeed(value) {
  return `${fmtBytes(value)}/s`;
}

export function fmtEta(seconds) {
  const s = Number(seconds) || 0;
  if (s <= 0 || s > 7 * 24 * 3600) return '—';
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = Math.floor(s % 60);
  if (h) return `${h} h ${m} min`;
  if (m) return `${m} min ${sec} s`;
  return `${sec} s`;
}

export function fmtDate(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString('es-ES', {
    day: '2-digit', month: 'short', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  });
}

export function fmtShortDate(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleDateString('es-ES', {
    day: '2-digit', month: 'short', year: 'numeric',
  });
}

export function fmtRelative(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  const diff = Math.round((Date.now() - date.getTime()) / 1000);
  if (diff < 45) return 'ahora';
  if (diff < 3600) return `hace ${Math.floor(diff / 60)} min`;
  if (diff < 86400) return `hace ${Math.floor(diff / 3600)} h`;
  if (diff < 7 * 86400) return `hace ${Math.floor(diff / 86400)} d`;
  return fmtDate(value);
}

export function fmtDuration(start, end) {
  if (!start) return '—';
  const a = new Date(start).getTime();
  const b = end ? new Date(end).getTime() : Date.now();
  if (Number.isNaN(a) || Number.isNaN(b)) return '—';
  const s = Math.max(0, Math.round((b - a) / 1000));
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.floor(s / 60)} min`;
  return `${Math.floor(s / 3600)} h ${Math.floor((s % 3600) / 60)} min`;
}

export function pct(value) {
  return Math.max(0, Math.min(100, Math.round((Number(value) || 0) * 100)));
}

export function clampPercent(value) {
  return Math.max(0, Math.min(100, Number(value) || 0));
}

export function initials(title) {
  const words = String(title || '?').trim().split(/\s+/).filter(Boolean);
  if (!words.length) return '?';
  if (words.length === 1) return words[0].slice(0, 1).toUpperCase();
  return `${words[0][0]}${words[1][0]}`.toUpperCase();
}

export function posterUrl(path, size = 'w342') {
  if (!path) return '';
  if (/^https?:\/\//i.test(path)) return path;
  return `https://image.tmdb.org/t/p/${size}${path}`;
}

export function episodeTag(item) {
  if (!item || item.kind !== 'series') return '';
  const season = Number(item.season || 0);
  const episode = Number(item.episode || 0);
  if (!season || !episode) return '';
  return `T${String(season).padStart(2, '0')}E${String(episode).padStart(2, '0')}`;
}

export function debounce(fn, ms = 250) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

export function queryString(params) {
  const qs = new URLSearchParams();
  for (const [key, value] of Object.entries(params || {})) {
    if (value === undefined || value === null || value === '' || value === false) continue;
    qs.set(key, String(value));
  }
  return qs.toString();
}

export function safeJson(value, fallback = null) {
  try { return JSON.parse(value); } catch (_) { return fallback; }
}
