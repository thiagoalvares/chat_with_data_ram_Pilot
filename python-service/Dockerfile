FROM python:3.12-slim

WORKDIR /app

# System deps occasionally needed by pandas/numpy wheels are already bundled;
# keep the image lean.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# Single-server, in-RAM design (Option 1): ONE worker process so the in-memory
# session store is shared across all requests; concurrency comes from THREADS
# (the workload is I/O-bound, waiting on the LLM gateway). Do NOT increase
# --workers with the memory backend — separate workers have separate memory and
# would split user sessions.
# app:app  -> module app.py, Flask object `app`
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "16", "--timeout", "120", "app:app"]
