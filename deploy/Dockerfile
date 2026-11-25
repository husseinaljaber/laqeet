FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-ara && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app app
COPY frontend frontend
# Source list and the snapshot a fresh database boots from (data/pdfs and the live DB are not in git).
COPY data/channels.json data/moad_seed.json data/seed_jobs.jsonl.gz data/
ENV DB_PATH=/data/laqeet.db PDF_DIR=/data/pdfs
