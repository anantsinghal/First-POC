FROM python:3.12-slim

# Non-root runtime user (uid 10001)
RUN useradd --create-home --uid 10001 --user-group --shell /usr/sbin/nologin scanner

WORKDIR /app

# Layer-friendly copy: metadata first, then package tree
COPY pyproject.toml README.md ./
COPY app/ ./app/
COPY data/ ./data/
COPY docs/ ./docs/

# Packaging backend is required for `pip install .[extras]` (not declared in host pyproject
# by design — only core-deps edits are allowed there). setuptools auto-finds the app package.
RUN pip install --no-cache-dir --upgrade pip setuptools wheel \
    && printf '\n[build-system]\nrequires = ["setuptools>=61"]\nbuild-backend = "setuptools.build_meta"\n\n[tool.setuptools.packages.find]\ninclude = ["app*"]\n' >> pyproject.toml \
    && pip install --no-cache-dir ".[ml,scale]"

ENV SCANNER_DATA_DIR=/data
RUN mkdir -p /data && chown scanner:scanner /data

VOLUME /data
EXPOSE 8000

USER scanner

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health')"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
