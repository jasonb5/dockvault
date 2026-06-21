FROM python:3.12-slim

ARG DOCKVAULT_VERSION=0.0.0

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SETUPTOOLS_SCM_PRETEND_VERSION=${DOCKVAULT_VERSION} \
    SETUPTOOLS_SCM_PRETEND_VERSION_FOR_DOCKVAULT=${DOCKVAULT_VERSION}

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir .

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"

CMD ["dockvault", "server"]
