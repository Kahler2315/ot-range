# Cedar Hollow historian ingest. See historian/ingest.py.
FROM python:3.11-slim

WORKDIR /app

COPY historian/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY historian/ingest.py historian/schema.sql ./

ENTRYPOINT ["python", "ingest.py"]
