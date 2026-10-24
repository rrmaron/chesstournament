FROM python:3.12-slim

WORKDIR /app

# Install minimal dependencies; stockfish powers the post-game engine review
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates stockfish && \
    rm -rf /var/lib/apt/lists/*
ENV STOCKFISH_PATH=/usr/games/stockfish

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Make bbpPairings executable (critical for Linux)
RUN chmod +x ./bbpPairings

EXPOSE 10000

# Production command
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "10000", "--log-level", "info"]