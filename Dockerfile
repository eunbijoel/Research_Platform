# Research Platform — Streamlit UI (team access via published port)
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app \
    TMPDIR=/tmp \
    PORT=8505

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        ffmpeg \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY app.py ./
COPY research_memory ./research_memory
COPY coding_agent ./coding_agent
COPY telegram_memory ./telegram_memory

EXPOSE 8505

CMD ["bash", "-lc", "streamlit run app.py --server.port=${PORT:-8505} --server.address=0.0.0.0 --browser.gatherUsageStats=false --server.headless=true"]
