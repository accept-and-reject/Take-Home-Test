FROM python:3.12.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.lock /app/requirements.lock
RUN python -m pip install --no-cache-dir --requirement /app/requirements.lock

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app

COPY --chown=appuser:appuser . /app

USER appuser
EXPOSE 8000

CMD ["python", "-m", "services.knowledge_service.entrypoint"]
