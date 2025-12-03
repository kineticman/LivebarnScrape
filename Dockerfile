# Use Python 3.11 slim as base
FROM python:3.11-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Install system dependencies
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    ca-certificates \
    procps \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Streamlink
RUN pip install --no-cache-dir streamlink

# Set working directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright and Chromium browser
RUN playwright install --with-deps chromium

# Copy application files
COPY livebarn_manager.py .
COPY build_catalog.py .
COPY refresh_single.py .
COPY schedule_utils.py .
COPY entrypoint.sh .

# Copy schedule providers module
COPY schedule_providers/ /app/schedule_providers/

# Make entrypoint executable
RUN chmod +x entrypoint.sh

# Create data directory
RUN mkdir -p /data

# Expose port
EXPOSE 5000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
  CMD curl -f http://localhost:5000/ || exit 1

# Run the manager via entrypoint
ENTRYPOINT ["./entrypoint.sh"]
