#!/usr/bin/env bash
# Instala TOD como servicio único en este servidor.
# Uso:
#   sudo bash deploy/install-unified.sh
# Variables opcionales:
#   TOD_USER, TOD_GROUP, TOD_HOST, TOD_PORT, ROOT
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
TOD_USER="${TOD_USER:-${SUDO_USER:-$(id -un)}}"
TOD_GROUP="${TOD_GROUP:-$(id -gn "$TOD_USER" 2>/dev/null || id -gn)}"
TOD_HOST="${TOD_HOST:-0.0.0.0}"
TOD_PORT="${TOD_PORT:-8773}"
UNIT_DIR="${UNIT_DIR:-/etc/systemd/system}"

if [[ ! -f "$ROOT/deploy/tod.service.in" ]]; then
  echo "no encuentro $ROOT/deploy/tod.service.in" >&2
  exit 1
fi
if [[ ! -f "$ROOT/pyproject.toml" ]]; then
  echo "$ROOT no parece la raíz de TOD" >&2
  exit 1
fi

# ReadWritePaths de systemd exige que var/ exista antes de arrancar.
install -d -o "$TOD_USER" -g "$TOD_GROUP" "$ROOT/var"

# Entorno de arranque (solo si no existe).
if [[ ! -f /etc/tod.env && ! -f /etc/catalogo.env ]]; then
  install -m 640 -o root -g "$TOD_GROUP" \
    "$ROOT/deploy/tod.env.example" /etc/tod.env
  echo "Creado /etc/tod.env: edítalo o configura todo desde la web."
fi

# Rutas de datos configuradas en TOD; systemd debe poder escribirlas.
MEDIA_PATHS="$(
  cd "$ROOT" && python3 - <<'PY' 2>/dev/null
from catalogo.config import Config

config = Config.load()
paths = {
    config.catalog_library,
    config.library_root,
    config.downloads_root,
    config.ondemand_save_path,
    config.seed_root,
    *config.strm_roots,
}
print(" ".join(f"-{path}" for path in sorted({str(p) for p in paths})))
PY
)"
MEDIA_PATHS="${MEDIA_PATHS:--/mnt/catalogo -/mnt/library -/mnt/descargas -/mnt/cache}"

sed \
  -e "s|@ROOT@|$ROOT|g" \
  -e "s|@USER@|$TOD_USER|g" \
  -e "s|@GROUP@|$TOD_GROUP|g" \
  -e "s|@HOST@|$TOD_HOST|g" \
  -e "s|@PORT@|$TOD_PORT|g" \
  -e "s|@MEDIA_PATHS@|$MEDIA_PATHS|g" \
  "$ROOT/deploy/tod.service.in" > "$UNIT_DIR/tod.service"
chmod 644 "$UNIT_DIR/tod.service"

# La web, el proxy y los timers pasan a ser responsabilidad de tod.service.
LEGACY=(
  catalogo-proxy.service
  catalogo-web.service
  catalogo-worker.timer
  catalogo-worker.service
  catalogo-rss.timer
  catalogo-rss.service
  catalogo-monitor.timer
  catalogo-monitor.service
  catalogo-backfill.timer
  catalogo-backfill.service
  catalogo-bridge.timer
  catalogo-bridge.service
  catalogo-restore.timer
  catalogo-restore.service
  catalogo-leases.timer
  catalogo-leases.service
  catalogo-qbit-limits.timer
  catalogo-qbit-limits.service
)
for unit in "${LEGACY[@]}"; do
  systemctl disable --now "$unit" 2>/dev/null || true
done

systemctl daemon-reload
systemctl enable tod.service
systemctl restart tod.service
systemctl --no-pager --lines=0 status tod.service || true

echo
echo "TOD escucha en http://$TOD_HOST:$TOD_PORT (web + reproducción + scheduler)."
echo "Configura Prowlarr, Jackett, qBittorrent, Jellyfin y directorios en la web."
echo "Los timers antiguos quedaron desactivados; no los vuelvas a habilitar."
