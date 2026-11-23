import { api, apiPost } from '../api.js';
import { esc } from '../format.js';
import { $, icon, setHeader, status, toast } from '../ui.js';

const STEPS = [
  {
    key: 'qbittorrent',
    title: 'qBittorrent',
    help: 'Descarga, siembra y sirve los torrents. Es la base de la reproducción bajo demanda.',
    required: true,
    docker: 'En Docker: http://qbittorrent:8080',
  },
  {
    key: 'sources',
    title: 'Fuentes',
    help: 'Prowlarr o Jackett descubren las versiones. Basta con uno de los dos.',
    required: true,
    docker: 'En Docker: Prowlarr http://prowlarr:9696 · Jackett http://jackett:9117',
  },
  {
    key: 'directories',
    title: 'Directorios',
    help: 'Dónde escribe TOD los .strm, las descargas y las semillas. Deben existir y ser escribibles.',
    required: true,
    docker: 'En Docker: /media/catalogo y /downloads',
  },
  {
    key: 'jellyfin',
    title: 'Jellyfin',
    help: 'Opcional, pero es donde se ven los títulos. Sin Jellyfin, TOD sigue funcionando como catálogo.',
    required: false,
    docker: 'En Docker: http://jellyfin:8096',
  },
];

function input(name, label, value, { type = 'text', placeholder = '', hint = '' } = {}) {
  return `<label class="setup-field">
    <span>${esc(label)}</span>
    <input class="input" data-setup-field="${esc(name)}" type="${type}"
      value="${esc(value || '')}" placeholder="${esc(placeholder)}"
      autocomplete="off" spellcheck="false">
    ${hint ? `<small>${esc(hint)}</small>` : ''}
  </label>`;
}

function secretField(name, label, configured, hint = '') {
  return `<label class="setup-field">
    <span>${esc(label)}</span>
    <input class="input" data-setup-field="${esc(name)}" type="password"
      placeholder="${configured ? 'Configurado, dejar vacío para no cambiar' : 'Sin configurar'}"
      autocomplete="new-password">
    ${hint ? `<small>${esc(hint)}</small>` : ''}
  </label>`;
}

function stepDone(state, key) {
  return Boolean(state.data?.steps?.[key]?.done);
}

export async function renderSetup({ signal }) {
  let state = {
    data: await api('/api/setup', { signal }),
    step: 0,
    mode: 'prowlarr',
    finished: false,
    busy: false,
  };
  if (state.data.steps.sources.jackett.has_secret && !state.data.steps.sources.prowlarr.has_secret) {
    state.mode = 'jackett';
  }
  const firstMissing = STEPS.findIndex(
    (step) => step.required && !stepDone(state, step.key),
  );
  state.step = firstMissing >= 0 ? firstMissing : 0;
  state.finished = firstMissing < 0;
  setHeader('Primeros pasos');

  function paint() {
    const active = STEPS[state.step];
    const info = state.data.steps[active.key] || {};
    $('#view').innerHTML = `<div class="setup-shell">
      <div class="setup-rail" role="navigation" aria-label="Pasos de configuración">
        ${STEPS.map((step, index) => `<button
          class="setup-rail-step ${index === state.step ? 'active' : ''} ${stepDone(state, step.key) ? 'done' : ''}"
          data-setup-step="${index}" aria-current="${index === state.step ? 'step' : 'false'}">
          <span class="setup-dot">${stepDone(state, step.key) ? icon('check', 'sm') : index + 1}</span>
          <span>${esc(step.title)}</span>
        </button>`).join('')}
      </div>

      ${state.finished ? finishView() : `<div class="panel setup-card">
        <div class="panel-head">
          <h2>Paso ${state.step + 1} de ${STEPS.length} · ${esc(active.title)}</h2>
          ${stepDone(state, active.key) ? status('Configurado', 'ok') : status('Pendiente', 'muted')}
        </div>
        <div class="panel-body">
          <p class="muted tiny" style="margin-top:0">${esc(active.help)}</p>
          <p class="setup-docker tiny faint">${esc(active.docker)}</p>
          ${stepFields(active, info)}
          <div class="setup-result" id="setupResult" role="status"></div>
          <div class="setup-actions">
            <button class="btn" data-setup-test>${icon('check', 'sm')} Probar conexión</button>
            <button class="btn btn-primary" data-setup-save>Validar y continuar</button>
            ${active.required ? '' : '<button class="btn btn-ghost" data-setup-skip>Saltar este paso</button>'}
          </div>
          <div class="setup-foot">
            <button class="btn btn-ghost btn-sm" data-setup-later>Configurar más tarde</button>
            <a class="btn btn-ghost btn-sm" href="#/settings">Configuración avanzada</a>
          </div>
        </div>
      </div>`}
    </div>`;
    bind();
  }

  function stepFields(step, info) {
    if (step.key === 'qbittorrent') {
      return `<div class="setup-grid">
        ${input('qbittorrent_url', 'URL de qBittorrent', info.url, { placeholder: 'http://127.0.0.1:8080', hint: 'Sin /api/v2.' })}
        ${input('qbittorrent_user', 'Usuario', info.user, { placeholder: 'admin' })}
        ${secretField('qbittorrent_pass', 'Contraseña', info.has_secret)}
      </div>`;
    }
    if (step.key === 'sources') {
      const prowlarr = info.prowlarr || {};
      const jackett = info.jackett || {};
      return `<div class="setup-choice-grid">
        <label class="setup-choice ${state.mode === 'prowlarr' ? 'active' : ''}">
          <input type="radio" name="setupSource" value="prowlarr" ${state.mode === 'prowlarr' ? 'checked' : ''}>
          <span><strong>Prowlarr</strong><small>${prowlarr.has_secret ? 'Ya configurado' : 'Recomendado'}</small></span>
        </label>
        <label class="setup-choice ${state.mode === 'jackett' ? 'active' : ''}">
          <input type="radio" name="setupSource" value="jackett" ${state.mode === 'jackett' ? 'checked' : ''}>
          <span><strong>Jackett</strong><small>${jackett.has_secret ? 'Ya configurado' : 'Alternativa'}</small></span>
        </label>
      </div>
      ${state.mode === 'prowlarr' ? `<div class="setup-grid">
        ${input('prowlarr_url', 'URL de Prowlarr', prowlarr.url, { placeholder: 'http://127.0.0.1:9696/api/v1' })}
        ${secretField('prowlarr_api_key', 'API key', prowlarr.has_secret)}
      </div>` : `<div class="setup-grid">
        ${input('jackett_url', 'URL de Jackett', jackett.url, { placeholder: 'http://127.0.0.1:9117/api/v2.0' })}
        ${secretField('jackett_api_key', 'API key', jackett.has_secret)}
      </div>`}`;
    }
    if (step.key === 'directories') {
      const paths = info.paths || {};
      return `<div class="setup-grid setup-grid-paths">
        ${input('catalog_library', 'Carpeta de los .strm', paths.catalog_library?.value, { placeholder: '/media/catalogo' })}
        ${input('downloads_root', 'Descargas', paths.downloads_root?.value, { placeholder: '/downloads' })}
        ${input('seed_root', 'Semillas', paths.seed_root?.value, { placeholder: '/downloads/catalogo' })}
      </div>`;
    }
    return `<div class="setup-grid">
      ${input('jellyfin_url', 'URL de Jellyfin', info.url, { placeholder: 'http://127.0.0.1:8096' })}
      ${secretField('jellyfin_api_key', 'API key', info.has_secret, 'Se crea en Jellyfin → Panel → API keys.')}
    </div>`;
  }

  function finishView() {
    const missing = STEPS.filter(
      (step) => step.required && !stepDone(state, step.key),
    );
    return `<div class="panel setup-done">
      <span class="setup-done-mark">${icon('check')}</span>
      <h2>${missing.length ? 'Casi está' : 'TOD está listo'}</h2>
      <p class="muted">${missing.length
        ? `Faltan pasos obligatorios: ${missing.map((step) => step.title).join(', ')}. Puedes completarlos ahora o más tarde.`
        : 'Ya puedes añadir títulos, buscar fuentes y verlos en Jellyfin.'}</p>
      <div class="setup-actions" style="justify-content:center">
        ${missing.length ? '<button class="btn" data-setup-back>Volver a los pasos</button>' : ''}
        <a class="btn btn-primary" href="#/dashboard">Ir a Inicio</a>
        <a class="btn btn-ghost" href="#/add">Añadir contenido</a>
        <a class="btn btn-ghost" href="#/settings">Abrir Configuración</a>
      </div>
    </div>`;
  }

  function collect() {
    const value = (name) => document.querySelector(`[data-setup-field="${name}"]`)?.value?.trim() || '';
    const secret = (name) => {
      const raw = value(name);
      return raw ? { [name]: raw } : {};
    };
    const step = STEPS[state.step];
    if (step.key === 'qbittorrent') {
      return {
        values: {
          qbittorrent_url: value('qbittorrent_url'),
          qbittorrent_user: value('qbittorrent_user'),
          ...secret('qbittorrent_pass'),
        },
        endpoint: 'qbittorrent',
      };
    }
    if (step.key === 'sources') {
      if (state.mode === 'prowlarr') {
        return {
          values: { prowlarr_url: value('prowlarr_url'), ...secret('prowlarr_api_key') },
          endpoint: 'prowlarr',
        };
      }
      return {
        values: { jackett_url: value('jackett_url'), ...secret('jackett_api_key') },
        endpoint: 'jackett',
      };
    }
    if (step.key === 'directories') {
      return {
        values: {
          catalog_library: value('catalog_library'),
          downloads_root: value('downloads_root'),
          seed_root: value('seed_root'),
        },
        endpoint: 'directories',
      };
    }
    return {
      values: { jellyfin_url: value('jellyfin_url'), ...secret('jellyfin_api_key') },
      endpoint: 'jellyfin',
    };
  }

  function result(text, tone = 'info') {
    const box = $('#setupResult');
    if (!box) return;
    box.className = `setup-result ${tone}`;
    box.textContent = text;
  }

  function busy(value) {
    state.busy = value;
    document.querySelectorAll('[data-setup-test], [data-setup-save]').forEach((button) => {
      button.disabled = value;
    });
  }

  async function runTest(values, endpoint) {
    result('Probando conexión…', 'info');
    const data = await apiPost(`/api/integrations/${endpoint}/test`, { values });
    const detail = data.detail || data.error || (data.ok ? 'Conexión correcta' : 'No se pudo conectar');
    result(detail, data.ok ? 'ok' : 'bad');
    return { ok: Boolean(data.ok), detail };
  }

  async function test() {
    const { values, endpoint } = collect();
    busy(true);
    try {
      await runTest(values, endpoint);
    } catch (error) {
      result(error.message, 'bad');
    } finally {
      busy(false);
    }
  }

  async function save() {
    const { values, endpoint } = collect();
    if (!Object.values(values).some(Boolean)) {
      result('Completa al menos un campo antes de continuar.', 'warn');
      return;
    }
    busy(true);
    try {
      const checked = await runTest(values, endpoint);
      if (!checked.ok) {
        result(`No se guardó: ${checked.detail}`, 'bad');
        busy(false);
        return;
      }
      await apiPost('/api/settings', { values });
      state.data = await api('/api/setup');
      toast('Paso guardado y validado', 'ok');
      if (state.step < STEPS.length - 1) state.step += 1;
      else state.finished = true;
      paint();
    } catch (error) {
      result(error.message, 'bad');
      busy(false);
    }
  }

  function bind() {
    document.querySelectorAll('[data-setup-step]').forEach((button) => {
      button.addEventListener('click', () => {
        state.step = Number(button.dataset.setupStep);
        state.finished = false;
        paint();
      });
    });
    document.querySelectorAll('input[name="setupSource"]').forEach((radio) => {
      radio.addEventListener('change', () => {
        state.mode = radio.value;
        paint();
      });
    });
    document.querySelector('[data-setup-test]')?.addEventListener('click', test);
    document.querySelector('[data-setup-save]')?.addEventListener('click', save);
    document.querySelector('[data-setup-skip]')?.addEventListener('click', () => {
      state.finished = true;
      paint();
    });
    document.querySelector('[data-setup-back]')?.addEventListener('click', () => {
      state.step = Math.max(0, STEPS.findIndex((step) => step.required && !stepDone(state, step.key)));
      state.finished = false;
      paint();
    });
    document.querySelector('[data-setup-later]')?.addEventListener('click', () => {
      try { sessionStorage.setItem('tod.setup.skip', '1'); } catch (_) { /* sin storage */ }
      location.hash = '#/dashboard';
    });
  }

  paint();
}
