FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Tashkent

WORKDIR /srv
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .

CMD ["python", "run.py"]
