FROM python:3.11-slim

# Ensure Python logs are written directly to stdout/stderr
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Update OS packages to reduce known vulnerabilities
RUN apt-get update && \
    apt-get upgrade -y && \
    rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Create a dedicated non-root user with a fixed UID
RUN useradd --create-home \
    --uid 10001 \
    --shell /usr/sbin/nologin \
    appuser

# Copy application
COPY main.py .

# Run the application as a non-root user
USER 10001

EXPOSE 8000

CMD ["opentelemetry-instrument", "--traces_exporter", "otlp", "--metrics_exporter", "none", "--exporter_otlp_endpoint", "http://otel-collector.monitoring:4317", "--service_name", "order-service", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]