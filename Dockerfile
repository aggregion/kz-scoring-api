FROM python:3.11-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

COPY requirements.txt ./
RUN pip install --prefix=/install -r requirements.txt

COPY pyproject.toml ./
COPY src ./src
RUN pip install --prefix=/install --no-deps .


FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    KZ_SCORING_HOST=0.0.0.0 \
    KZ_SCORING_PORT=8000

RUN useradd --create-home --uid 10001 app

COPY --from=builder /install /usr/local

WORKDIR /home/app
USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; \
import os; \
url='http://127.0.0.1:'+os.environ.get('KZ_SCORING_PORT','8000')+'/healthz'; \
sys.exit(0 if urllib.request.urlopen(url, timeout=2).status==200 else 1)" || exit 1

ENTRYPOINT ["python", "-m", "kz_scoring_api"]
