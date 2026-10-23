# Cloud Run 用イメージ（チャット API のサービスと、入稿のジョブで共用）
#   ジョブ:  sensei job ingest gs://<bucket>/raw/<file>
# Docker Hub の取得回数制限を避けるため Google のミラーを使う
FROM mirror.gcr.io/library/python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install ".[gcp]" \
    && useradd --create-home --uid 10001 sensei

USER sensei
ENTRYPOINT ["sensei"]
CMD ["--help"]
