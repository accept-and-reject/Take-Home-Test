FROM python:3.12.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY demo/requirements.lock /app/demo/requirements.lock
RUN python -m pip install --no-cache-dir --requirement /app/demo/requirements.lock

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/logs/demo \
    && chown -R appuser:appuser /app

COPY --chown=appuser:appuser demo /app/demo
COPY --chown=appuser:appuser shared /app/shared

USER appuser

ENTRYPOINT ["python", "demo/run_demo.py"]
