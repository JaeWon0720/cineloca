# 씨네로케 API 서버 이미지 (COMP-02~15). 팀 표준 Python 3.10
FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app

# 관리자 권한이 아닌 사용자로 실행
RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 8000

# 워커 1개: F4 대화 맥락을 서버 메모리에 두기 때문 (ADR-013)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
