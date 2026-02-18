FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl supervisor && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir poetry && \
    poetry config virtualenvs.create false

COPY orchestrator/pyproject.toml orchestrator/poetry.lock* ./
RUN poetry install --no-interaction --no-ansi --only main --no-root

COPY orchestrator/supervisord.conf /etc/supervisor/conf.d/supervisord.conf
COPY orchestrator/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

COPY orchestrator/src/ ./src/
COPY orchestrator/dashboard/ ./dashboard/

# Bake default config — entrypoint copies it to /app/data/config.yaml on first start
COPY config.yaml ./default_config.yaml

EXPOSE 8501

ENTRYPOINT ["/entrypoint.sh"]
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
