# Seguridad

TOD está pensado para una red doméstica o WireGuard, no para exponerse
directamente a Internet.

- No publiques el puerto `8773` en Internet. Usa WireGuard o un reverse proxy
  con TLS y autenticación.
- Define `TOD_WEB_TOKEN` si el panel queda accesible desde la LAN.
- Los secretos (qBittorrent, Prowlarr, Jackett, Jellyfin, TMDB) se guardan en
  `var/tod.db` con permisos `0600`; el `.env` y `var/` están ignorados por
  git. En Docker, la base está en `/config/tod.db`.
- `/prepare` y `/lease` solo aceptan peticiones desde loopback.
- `download=1` en `/play` inicia una descarga explícita. Las rutas de
  reproducción (`/play`, `/resolve`, `/sources`, `/status`) no usan el token
  del panel: no expongas el puerto fuera de la LAN o WireGuard.
- Si encuentras una vulnerabilidad, no abras un issue público con credenciales
  ni rutas privadas. Contacta con el mantenedor del repositorio.
