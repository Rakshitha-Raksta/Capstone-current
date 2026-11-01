FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r backend/requirements.txt

# Application code + the cited rule dataset + reference data + synthetic demo data.
# Ground-truth labels are NOT copied (runtime never needs them; see .dockerignore).
COPY backend/ backend/
COPY data/structured/ data/structured/
COPY data/raw/ data/raw/
COPY data/reference/ data/reference/
COPY data/processed/ data/processed/
COPY data/synthetic/demo/ data/synthetic/demo/
COPY scripts/ scripts/

RUN useradd --create-home --uid 10001 clearclaim && chown -R clearclaim /app
USER clearclaim
WORKDIR /app/backend
EXPOSE 5001

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5001/api/v1/health').status==200 else 1)"

# Initialise (idempotent: existing encounters are skipped) then serve.
CMD ["sh", "-c", "python ../scripts/setup_demo.py && exec uvicorn app.main:app --host 0.0.0.0 --port 5001 --workers 1"]
