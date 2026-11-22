FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY catalogo ./catalogo

EXPOSE 8773

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8773/healthz', timeout=3)"

CMD ["python", "-m", "catalogo.cli", "service", "--host", "0.0.0.0", "--port", "8773"]
