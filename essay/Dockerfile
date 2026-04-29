FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Expose all ports
EXPOSE 8000 8001 8002

# Run all 3 FastAPI servers
CMD bash -c "uvicorn fastapi_app:app --host 0.0.0.0 --port 8000 & \
             uvicorn quiz_fastapi_app:app --host 0.0.0.0 --port 8001 & \
             uvicorn para_read_fastapi:app --host 0.0.0.0 --port 8002 & \
             uvicorn api_gateway:app --host 0.0.0.0 --port 8080 & \
             wait"