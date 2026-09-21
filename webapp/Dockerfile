# Container image for the hosted PDF editor.
# The port comes from PORT when the host sets one (Render, Cloud Run) and
# falls back to 7860, which is what Hugging Face Spaces expects.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/home/app

# A non-root user, which is also what Hugging Face Spaces expects.
RUN useradd --create-home --uid 1000 app
WORKDIR /app

COPY webapp/requirements.txt /app/webapp/requirements.txt
RUN pip install --no-cache-dir -r /app/webapp/requirements.txt

COPY pdf_editor /app/pdf_editor
COPY webapp /app/webapp

RUN chown -R app:app /app
USER app

ENV PORT=7860
EXPOSE 7860

HEALTHCHECK --interval=60s --timeout=5s --start-period=30s \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '7860') + '/healthz')"

CMD ["sh", "-c", "uvicorn webapp.server:app --host 0.0.0.0 --port ${PORT:-7860} --workers 1"]
