FROM python:3.11-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app:/app/backend
ENV FLOWCITY_EMBEDDING_CACHE_DIR=/models/fastembed
ARG FLOWCITY_BUNDLE_EMBEDDING=false

COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

COPY . /app
RUN if [ "$FLOWCITY_BUNDLE_EMBEDDING" = "true" ]; then \
      FLOWCITY_EMBEDDING_ENABLED=true python /app/initialize_embeddings.py; \
    fi

WORKDIR /app/backend
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
