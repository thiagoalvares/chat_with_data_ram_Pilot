"""
Production entry point for the single-server, in-RAM deployment (Option 1).

Runs the Flask app under waitress in ONE process with a thread pool. A single
process is required because sessions live in that process's memory (the
`memory` storage backend) — all requests must share the same memory space.
Concurrency comes from threads, which is ideal here because each request spends
most of its time waiting on the LLM gateway (I/O-bound).

Why waitress: it is a production-grade WSGI server that runs on Windows and
Linux alike (gunicorn is Linux-only). This makes it the natural fit for running
Python alongside the .NET/IIS app on the same Windows server.

Run:
    python serve.py
Environment:
    SERVICE_HOST      bind address (default 127.0.0.1 — localhost only, since
                      the .NET app on the same box proxies to it)
    SERVICE_PORT      port (default 8000)
    WAITRESS_THREADS  thread pool size (default 16)
"""

import os

from waitress import serve

from app import app  # the Flask app (memory backend by default)


if __name__ == "__main__":
    host    = os.environ.get("SERVICE_HOST", "127.0.0.1")
    port    = int(os.environ.get("SERVICE_PORT", "8000"))
    threads = int(os.environ.get("WAITRESS_THREADS", "16"))
    print(f"Chat with Data (in-RAM) — waitress on http://{host}:{port} "
          f"({threads} threads, single process)")
    serve(app, host=host, port=port, threads=threads)
