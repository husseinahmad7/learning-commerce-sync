FROM docker.io/library/python:3.13-slim@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f AS dependencies

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

COPY requirements.lock /build/requirements.lock
COPY .wheelhouse /wheelhouse
RUN if find /wheelhouse -name '*.whl' -print -quit | grep -q .; then \
      pip install --no-index --find-links=/wheelhouse --prefix=/install --no-compile --no-cache-dir --require-hashes --only-binary=:all: -r /build/requirements.lock; \
    else \
      pip install --index-url https://pypi.org/simple --retries 3 --timeout 15 --prefix=/install --no-compile --no-cache-dir --require-hashes --only-binary=:all: -r /build/requirements.lock; \
    fi

FROM docker.io/library/python:3.13-slim@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin app \
    && mkdir -p /run/celery \
    && chown 10001:10001 /run/celery
COPY --from=dependencies /install /usr/local
WORKDIR /app
COPY config config
COPY checkout checkout
COPY mock_erp mock_erp
COPY scripts/health.py scripts/health.py
COPY manage.py entrypoint.sh ./
RUN chmod -R a-w /app && chmod 0555 /app/entrypoint.sh
USER 10001:10001
ENTRYPOINT ["./entrypoint.sh"]
CMD ["web"]
