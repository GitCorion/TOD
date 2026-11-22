let liveHandler = null;
let authHandler = null;

export function onLiveChange(handler) {
  liveHandler = handler;
}

export function onAuthRequired(handler) {
  authHandler = handler;
}

function setLive(ok) {
  if (liveHandler) liveHandler(ok);
}

export class ApiError extends Error {
  constructor(message, status, detail) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }
}

export async function api(path, options = {}) {
  const init = {
    method: options.method || 'GET',
    credentials: 'same-origin',
    headers: { 'X-Catalogo': 'web', Accept: 'application/json' },
  };
  if (options.signal) init.signal = options.signal;
  if (options.body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(options.body);
  }
  let response;
  try {
    response = await fetch(path, init);
  } catch (error) {
    if (error.name === 'AbortError') throw error;
    setLive(false);
    throw new ApiError('No se puede conectar con el servidor', 0);
  }
  let data = null;
  try { data = await response.json(); } catch (_) { /* respuestas vacías */ }
  if (response.status === 401) {
    setLive(true);
    if (authHandler) authHandler();
    throw new ApiError('sesión requerida', 401, data);
  }
  if (!response.ok) {
    setLive(true);
    const rawDetail = data && data.detail;
    const detail = rawDetail !== undefined && rawDetail !== null && rawDetail !== ''
      ? (typeof rawDetail === 'string' ? rawDetail : JSON.stringify(rawDetail))
      : '';
    throw new ApiError(
      `${(data && data.error) || `HTTP ${response.status}`}${detail ? `: ${detail}` : ''}`,
      response.status,
      data && data.detail,
    );
  }
  setLive(true);
  return data;
}

export function apiPost(path, body = {}, options = {}) {
  return api(path, { ...options, method: 'POST', body });
}
