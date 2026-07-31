# Cedar Hollow HMI. See hmi/app.py for why this exists instead of FUXA.
FROM python:3.11-slim

WORKDIR /app

COPY hmi/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY hmi/app.py .
COPY hmi/templates/ templates/

EXPOSE 8090

ENTRYPOINT ["python", "app.py"]
