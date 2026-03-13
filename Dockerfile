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
RUN pip install --no-cache-dir -r requirements.txt

# 🚨 6. THE PLAYWRIGHT MAGIC 🚨
# Install ONLY Chromium and its underlying Linux system dependencies.
# This ensures your headless browser works without bloating the image.
RUN playwright install chromium
RUN playwright install-deps chromium

# 7. Copy the rest of your backend code into the container
COPY . .

# 8. Cloud Run injects a $PORT environment variable (usually 8080). 
# We tell Uvicorn/FastAPI to listen on that exact port.
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}"]