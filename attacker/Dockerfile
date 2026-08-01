# M4: attacker tooling running as a real container on zone-enterprise,
# reaching the range only through the router — no --source-ip loopback
# trick needed here, the container's real assigned IP *is* the distinct
# source address the loopback stack had to fake. See scope.yml: this
# container's whole point is to be network-isolated from zone-ops except
# through router, which tests/test_router.py verifies.
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY common/ common/
COPY process_sim/ process_sim/
COPY plc/ plc/
COPY attacker/ attacker/

ENTRYPOINT ["python", "-m"]
