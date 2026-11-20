# Seguridad

TOD está pensado para una red doméstica o WireGuard, no para exponerse
directamente a Internet.

- No publiques el puerto `8773` en Internet. Usa WireGuard o un reverse proxy
  con TLS y autenticación.
- Define `CATALOGO_WEB_TOKEN` si el panel queda accesible desde la LAN.
- Los secretos (qBittorrent, Prowlarr, Jackett, Jellyfin, TMDB) se guardan en
  `var/catalogo.db` con permisos `0600`; el `.env` y `var/` están ignorados por
  git.
- `/prepare`, `/lease` y `/play?...&download=1` están restringidos a loopback.
- Si encuentras una vulnerabilidad, no abras un issue público con credenciales
  ni rutas privadas. Contacta con el mantenedor del repositorio.
