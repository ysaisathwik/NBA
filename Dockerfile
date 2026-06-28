FROM python:3.11-slim

WORKDIR /app

# curl is used by the container healthcheck
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Non-root user for security; give it ownership + a writable data dir for SQLite
RUN adduser --disabled-password --gecos "" appuser \
    && mkdir -p /app/data \
    && chown -R appuser /app
USER appuser

EXPOSE 8000

CMD ["uvicorn", "nba_platform.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
