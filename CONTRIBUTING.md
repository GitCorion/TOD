# Contribuir a TOD

1. Haz un fork y crea una rama: `git switch -c feat/cambio`.
2. Instala en modo editable: `python3 -m pip install -e .`.
3. Ejecuta los tests antes de abrir el PR:

```bash
python3 -m pytest -q
```

## Reglas

- No subas bases de datos, `.env`, claves de API ni rutas privadas.
- Añade tests para cambios de comportamiento.
- Mantén la compatibilidad con SQLite y Python 3.11+.
- Los secretos se guardan en la base local y nunca se devuelven por la API.
- No incluyas nombres de trackers privados, IPs ni credenciales en capturas,
  logs o documentación.

## Estructura

- `catalogo/`: backend, resolver, reproducción, scheduler y API.
- `catalogo/web_static/`: SPA sin dependencias externas.
- `migrations/`: esquema SQLite.
- `deploy/`: unidad systemd e instalador.
- `tests/`: suite de pytest.
