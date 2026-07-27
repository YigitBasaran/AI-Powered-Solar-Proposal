# Playwright's own image already carries Chromium and its system libraries,
# which is what the PDF renderer needs. Installing Chromium onto a bare slim
# image works too but pulls in a long list of shared libraries by hand.
FROM mcr.microsoft.com/playwright/python:v1.49.0-jammy

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first so a source edit does not invalidate the install layer.
COPY pyproject.toml ./
RUN python -m pip install --upgrade pip && \
    python -m pip install --no-cache-dir \
        "fastapi>=0.115" "uvicorn[standard]>=0.32" "pydantic>=2.9" \
        "pydantic-settings>=2.6" "sqlalchemy>=2.0.36" "alembic>=1.14" \
        "aiosqlite>=0.20" "greenlet>=3.1" "httpx>=0.28" "shapely>=2.0.6" \
        "numpy>=1.26" "jinja2>=3.1" "python-multipart>=0.0.20" "pillow>=10.3" \
        "playwright>=1.49"

COPY app ./app

# Fixtures are mounted read-only at /fixtures by compose and pointed at
# explicitly, rather than inferred from how deep the source file sits.
ENV FIXTURES_DIR_OVERRIDE=/fixtures
RUN mkdir -p /data /app/storage

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
