# App image for the API and the Temporal worker (same image, different commands).
FROM python:3.12-slim

WORKDIR /app

# Install the package + runtime deps. Copy metadata first for better layer caching.
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

# Default command runs the API; the worker service overrides this in compose.
EXPOSE 8000
CMD ["uvicorn", "lending.los.asgi:app", "--host", "0.0.0.0", "--port", "8000"]
