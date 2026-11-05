FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e ".[dev]" 2>/dev/null || pip install --no-cache-dir .

# Copy source
COPY gateway/ ./gateway/
COPY catalog/ ./catalog/
COPY schema.sql ./schema.sql

# Non-root user
RUN useradd -r -s /bin/false gateway && chown -R gateway:gateway /app
USER gateway

EXPOSE 9090

CMD ["uvicorn", "gateway.main:app", "--host", "0.0.0.0", "--port", "9090", "--log-level", "info"]
