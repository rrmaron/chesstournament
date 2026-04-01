FROM python:3.12-slim

# Install any system dependencies if needed (usually not for static bbpPairings)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy your app code
COPY . .

# Make the binary executable
RUN chmod +x ./bbpPairings

# Expose the port Render will use
EXPOSE 10000

# Render uses $PORT environment variable
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "10000"]
