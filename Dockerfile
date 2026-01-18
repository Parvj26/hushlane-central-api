FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY main.py .
COPY templates/ ./templates/

# Create data directory for SQLite database
RUN mkdir -p /app/data

# Create non-root user
RUN useradd -m -u 1000 apiuser && \
    chown -R apiuser:apiuser /app
USER apiuser

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8001/health')"

EXPOSE 8001

# Use PORT environment variable provided by Render (defaults to 8001 for local)
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8001}
