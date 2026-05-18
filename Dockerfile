FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY pyproject.toml requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt && pip install -e . --no-deps

COPY userbot/ ./userbot/

CMD ["python", "-m", "userbot"]
