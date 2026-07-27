# Python 3.12, not the Playwright base image.
#
# mcr.microsoft.com/playwright/python:v1.49.0-jammy ships Python 3.10, which
# cannot import StrEnum (3.11+). It built cleanly and then failed at startup,
# because an earlier revision of this file hand-duplicated the dependency list
# and so never enforced pyproject's `requires-python = ">=3.12"`.
#
# Dependencies are now installed FROM pyproject.toml. One list, one source of
# truth, and the interpreter constraint is checked at build time.
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

# Build-time only: Shapely needs GEOS headers, and playwright needs curl to
# fetch Chromium.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY app ./app

# Migrations ship with the image so an operator can run `alembic upgrade head`
# against the container's own database. The app itself creates tables at
# startup; test_schema_parity.py asserts the two cannot drift.
COPY alembic.ini ./
COPY migrations ./migrations

RUN python -m pip install --upgrade pip \
    && python -m pip install ".[pdf]"

# Chromium plus its system libraries, for PDF rendering.
RUN python -m playwright install --with-deps chromium \
    && apt-get purge -y build-essential \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# Fixtures are mounted read-only at /fixtures by compose and pointed at
# explicitly, rather than inferred from how deep the source file sits.
ENV FIXTURES_DIR_OVERRIDE=/fixtures
RUN mkdir -p /data /app/storage

EXPOSE 8000

# Fail fast and visibly if the interpreter is ever wrong again.
RUN python -c "import sys; assert sys.version_info[:2] >= (3, 12), sys.version; \
    from app.core.config import get_settings; get_settings(); print('startup import OK')"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
