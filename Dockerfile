# 1. Use an official, lightweight Python image
FROM python:3.13.9-slim

# 2. Stop Python from generating .pyc files and enable live logging
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# 3. Set the working directory inside the container
WORKDIR /app

# 4. Copy your requirements file first
COPY requirements.txt .

# 5. Install Python dependencies safely
# NOTE: this KEEPS the `playwright` Python PACKAGE (code imports it at startup).
# The thin API only removes the Chromium BINARY below — it never launches a
# browser (free-search moved to its own Service in B-2; the agent runs as a Job).
RUN pip install --no-cache-dir -r requirements.txt

# 🚫 6. Chromium BINARY intentionally NOT installed in the thin API image.
# After B-1 (agent → Job) and B-2 (free-search → its own Service), the API
# imports browser code but never calls browser.launch(). Imports succeed without
# the binary; this drops ~300MB+ from the image. The browser image
# (Dockerfile.worker) still installs Chromium for the workloads that need it.
#   REMOVED: RUN playwright install chromium
#   REMOVED: RUN playwright install-deps chromium

# 7. Copy the rest of your backend code into the container
COPY . .

# 8. Cloud Run injects a $PORT environment variable (usually 8080). 
# We tell Uvicorn/FastAPI to listen on that exact port.
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}"]