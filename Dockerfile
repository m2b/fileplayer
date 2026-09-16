# Build context is this repo's root, so the image only ever depends on
# what's in this repo.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Only the importable package - not tests/, demo/, sample data, or deploy
# scripts. "python -m publisher.player_cli" resolves publisher as a
# regular package relative to this working directory.
COPY publisher/ ./publisher/

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/tree', timeout=3)" || exit 1

ENTRYPOINT ["python", "-m", "publisher.player_cli"]
