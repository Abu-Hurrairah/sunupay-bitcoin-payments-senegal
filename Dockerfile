FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=10 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv

COPY requirements.txt .

# Cache downloaded wheels between builds. Retries/timeouts help on slow PyPI connections.
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    python -m pip install --prefer-binary \
    --timeout 120 --retries 10 \
    -r requirements.txt

COPY app ./app

RUN mkdir -p /appdata && chown -R 101:101 /appdata /srv

USER 101:101

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
