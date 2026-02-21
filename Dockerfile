FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY pyproject.toml README.md README.zh-CN.md LICENSE /app/
COPY src /app/src
COPY web /app/web
COPY scripts /app/scripts
COPY .env.example /app/.env.example

RUN pip install --upgrade pip && pip install -e .

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "awe_agentcheck.main:app", "--host", "0.0.0.0", "--port", "8000"]
