# GCP（Cloud Run）へのデプロイ手順

構成は [docs/design/06-deployment-gcp.md](../docs/design/06-deployment-gcp.md)。ここでは入稿ジョブを動かすまでの手順を示す。
チャット API（Cloud Run サービス）は実装後に追記する。

## 0. 変数

```bash
PROJECT=<your-project-id>
REGION=asia-northeast1
REPO=sensei                       # Artifact Registry のリポジトリ名
IMAGE=$REGION-docker.pkg.dev/$PROJECT/$REPO/sensei:latest
BUCKET=gs://$PROJECT-sensei       # 原本・成果物の置き場
SQL=sensei-db                     # Cloud SQL インスタンス名
SA=sensei-job@$PROJECT.iam.gserviceaccount.com
gcloud config set project $PROJECT
```

## 1. API の有効化

```bash
gcloud services enable run.googleapis.com sqladmin.googleapis.com artifactregistry.googleapis.com \
  cloudbuild.googleapis.com aiplatform.googleapis.com secretmanager.googleapis.com storage.googleapis.com
```

## 2. イメージのビルド

```bash
gcloud artifacts repositories create $REPO --repository-format=docker --location=$REGION
gcloud builds submit --tag $IMAGE .
```

## 3. Cloud Storage

```bash
gcloud storage buckets create $BUCKET --location=$REGION --uniform-bucket-level-access
```

配置: `$BUCKET/raw/` に原本、`$BUCKET/books/` に入稿の成果物。

## 4. Cloud SQL（PostgreSQL 16 + pgvector）

```bash
gcloud sql instances create $SQL --database-version=POSTGRES_16 --region=$REGION \
  --edition=ENTERPRISE --tier=db-custom-1-3840 --storage-auto-increase
gcloud sql databases create sensei --instance=$SQL
DB_PASSWORD=$(openssl rand -base64 24)
gcloud sql users create sensei --instance=$SQL --password="$DB_PASSWORD"
printf '%s' "$DB_PASSWORD" | gcloud secrets create sensei-db-password --data-file=-
```

pgvector 拡張（`CREATE EXTENSION vector`）とテーブルは、ジョブの初回実行時に `sensei db init` 相当の処理で作られる。

## 5. サービスアカウント

```bash
gcloud iam service-accounts create sensei-job
for role in roles/cloudsql.client roles/aiplatform.user roles/secretmanager.secretAccessor; do
  gcloud projects add-iam-policy-binding $PROJECT --member=serviceAccount:$SA --role=$role
done
gcloud storage buckets add-iam-policy-binding $BUCKET --member=serviceAccount:$SA --role=roles/storage.objectAdmin
```

## 6. 入稿ジョブの作成

```bash
gcloud run jobs create sensei-ingest --image=$IMAGE --region=$REGION \
  --service-account=$SA \
  --set-cloudsql-instances=$PROJECT:$REGION:$SQL \
  --set-secrets=SENSEI_DB_PASSWORD=sensei-db-password:latest \
  --set-env-vars="^@^SENSEI_DB_INSTANCE=$PROJECT:$REGION:$SQL@SENSEI_DB_USER=sensei@SENSEI_DB_NAME=sensei@SENSEI_OUT=$BUCKET/books@SENSEI_EMBEDDER=vertex:gemini-embedding-001?dim=768@GOOGLE_CLOUD_PROJECT=$PROJECT@SENSEI_VERTEX_LOCATION=$REGION" \
  --cpu=2 --memory=4Gi --task-timeout=3600 --max-retries=1
```

`--set-env-vars` の先頭の `^@^` は区切り文字を `@` に変える指定（値に `,` や `=` を含むため）。

## 7. 入稿の実行

```bash
gcloud storage cp book.pdf $BUCKET/raw/book.pdf
gcloud run jobs execute sensei-ingest --region=$REGION --wait \
  --args="job,ingest,$BUCKET/raw/book.pdf"
```

結果（QA・登録件数）はジョブのログに 1 冊 1 行の JSON で出る。成果物は `$BUCKET/books/{book_id}/v1/{extractor}/`。
同じ原本を再実行すると、Cloud Storage 上の manifest により済んだ段はスキップされる。

## ローカルでの確認

```bash
docker compose -f docker/compose.yml up -d
docker build -t sensei:dev .
docker run --rm --network host -v $PWD/books:/books:ro \
  -e SENSEI_DSN=postgresql://sensei:sensei@localhost:5432/sensei \
  sensei:dev job ingest /books/book.epub --out /tmp/out --embedder hash
```

Vertex AI をローカルから使う場合は `gcloud auth application-default login` のうえ、
`-v ~/.config/gcloud:/home/sensei/.config/gcloud:ro -e GOOGLE_CLOUD_PROJECT=$PROJECT` を付けて `--embedder vertex:gemini-embedding-001?dim=768` を指定する。

## 確認が必要な点

- **Vertex AI のモデルとリージョン**: 埋め込みモデル（`gemini-embedding-001` など）と Claude が `asia-northeast1` で使えるかは、使う時点の Vertex AI のドキュメントで確認する。使えない場合は `SENSEI_VERTEX_LOCATION` で別リージョン（Claude は `global` エンドポイントなど）を指定する（Cloud Run・Cloud SQL・Cloud Storage は東京のまま）。
- **埋め込みの次元**: pgvector の HNSW 索引は 2000 次元までのため、`dim=768`（または 1536）に落として使う。モデルや次元を変えた場合は `sensei embed` で別モデルとして追加でき、既存の埋め込みと並べて比較できる。
