# Field simulator container. Runs process_sim/server.py --field-only,
# polled by OpenPLC as a Modbus master (docker-compose.yml).
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY common/ common/
COPY process_sim/ process_sim/
COPY plc/ plc/

# 0.0.0.0 here is correct, not a contradiction of the loopback-only
# default elsewhere: this binds inside the container's own isolated
# network namespace, which is what lets other containers on the same
# docker-compose network reach it. Nothing is exposed to the host or the
# LAN unless docker-compose.yml explicitly publishes the port.
ENV MODBUS_BIND_HOST=0.0.0.0
EXPOSE 5502

ENTRYPOINT ["python", "-m", "process_sim.server", "--field-only"]
