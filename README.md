# 결 (Gyeol) — Data

패션 편집샵/브랜드샵에서 상품을 크롤링해 **Bronze → Silver → Gold → Public** 네 단계로 이관하는 데이터 파이프라인.

최종적으로 `public.product` 에는 상품명·카테고리·색상·소재·패턴·스타일·큐레이션 설명·bge-m3 임베딩(1024d)·MeCab 토크나이즈 검색 텍스트·S3 이미지 키까지 전부 준비된 상태로 적재된다.

---

## 파이프라인

```
crawlers/          ─→  store/bronze/{platform}/{date}/*.jsonl   (Bronze)
    │                                                            │
    ▼                                                            ▼
silver (etl/silver/)                                         staging.brand
    ─ raw name clean / base_name / color 정규식 추출          staging.product
    ─ 가격 검증, UPSERT
    ─ 후속 codex job 트리거 (brand_profile / category_review /
      base_name_review) — 빠진 category·base_name·색상을 LLM 이 채움
    │
    ▼
gold (etl/gold/)
    ─ enrichment.py     Gemini 멀티모달 → sub_category / fit /
                         description_curated / material / pattern /
                         details / style
    ─ embed.py          TEI(bge-m3) → embedding vector(1024)
    ─ tokenize.py       MeCab(NNG/NNP/SL/SN) → search_text
    │
    ▼
scripts/upload_images.py  로컬/URL → S3(public-read) → thumbnail_key/image_keys
    │
    ▼
publish (etl/publish/)
    ─ publishable 조건 만족한 row 만 public.brand/product INSERT
    ─ staging.{brand,product}.published = now()
    ─ 멱등 · row 단위 트랜잭션
```

### 오케스트레이션

Airflow 3.2.0 — per-platform DAG (`dags/_factory.py`) + gold DAG + reconciler.
`pipelines/{platform}.yaml` 매니페스트가 플랫폼별로 필요한 codex 스킬 / 실버 transform 을 선언한다.

---

## 디렉토리 구조

```
.
├── brands.yaml                   # 크롤 대상 (platform → brand_id → url)
├── common/                       # 공용 schema, constants, taxonomy
├── crawlers/                     # 플랫폼별 크롤러 (etcseoul, barbershop, sculpstore, modeman, bastong)
├── etl/
│   ├── silver/                   # Bronze → staging
│   ├── gold/                     # Gemini + embedding + MeCab tokenize
│   └── publish/                  # staging → public
├── migrations/                   # golang-migrate (raw SQL)
├── pipelines/                    # 플랫폼별 ETL 매니페스트
├── workers/codex/                # codex 통합 스킬 정의 (codex_skills.yaml)
├── orchestration/                # DAG 호출 가능한 얇은 함수들
├── dags/                         # Airflow DAG
├── scripts/                      # upload_images, download_images, etc.
├── skills/                       # 로컬 codex 스킬 (base_name_review)
├── docs/                         # 아키텍처 문서
├── docker-compose.yml            # postgres / embedding(TEI) / reranker / airflow 5-service
└── Dockerfile.airflow            # MeCab 포함 커스텀 Airflow 이미지
```

---

## 사전 요구 사항

- Docker + Docker Compose
- Python 3.12 + `uv` (`pip install uv`)
- golang-migrate CLI — 마이그레이션
- AWS S3 bucket (public read on 이미지 prefix) + IAM access key
- Gemini API key(s) — 여러 개 넣으면 자동 round-robin
- Codex CLI (`codex`) — silver 후속 LLM 스킬용

`.env`:
```dotenv
POSTGRES_PASSWORD=...
DB_URL=postgresql://gyeol:...@localhost:5432/gyeol

# Gemini — 숫자 suffix 로 여러 개 지정 (GEMINI_API_KEY1, 2, ...)
GEMINI_API_KEY1=...
GEMINI_API_KEY2=...
GEMINI_MIN_INTERVAL_PER_KEY=0.5     # 키당 최소 호출 간격 (초)

# S3
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_DEFAULT_REGION=ap-southeast-2
GYEOL_S3_BUCKET=gyeol
GYEOL_S3_PREFIX=products
GYEOL_S3_PUBLIC_BASE_URL=https://gyeol.s3.ap-southeast-2.amazonaws.com

# TEI
GYEOL_TEI_URL=http://localhost:8080
```

---

## 부팅

```bash
# 1. 인프라 기동 (postgres + TEI embedding + Airflow 5-service)
docker compose up -d postgres embedding airflow-init
docker compose up -d airflow-scheduler airflow-dag-processor airflow-triggerer airflow-api-server

# 2. 마이그레이션
make migrate-up

# 3. 의존성
uv sync
```

Airflow UI: http://localhost:8082

---

## 수동 실행 (CLI)

```bash
# Crawl → Bronze
uv run python -m crawlers.run                          # 전체
uv run python -m crawlers.run --platform etcseoul      # 플랫폼만
uv run python -m crawlers.run --brand artifacts        # 브랜드만
uv run python -m crawlers.run --retry                  # 실패 재시도

# Silver (Bronze → staging)
uv run python -m etl.silver.run                        # 전체, codex 스킬 autostart 포함
uv run python -m etl.silver.run --no-brand-profile --no-category-review --no-training-data

# Gold — Gemini enrichment (전체)
uv run python main.py gold --workers 20

# Gold — Embedding
uv run python -m etl.gold.embed        # ← 없으면 다음 한 줄 써도 됨
# 또는:
uv run python -c "from etl.gold.embed import embed_all; print(embed_all())"

# Gold — MeCab tokenize (컨테이너 안에서)
docker exec airflow-scheduler python -c \
  "from etl.gold.tokenize import tokenize_all; print(tokenize_all())"

# Image upload (local+URL → S3)
uv run python scripts/upload_images.py

# Publish — staging → public
uv run python -m etl.publish                          # 한 번만
uv run python -m etl.publish --watch --interval 300   # 폴링 데몬 (cron 대체)
```

---

## 스키마 요점

### `staging.brand`

| raw | curated |
|---|---|
| `description` — Bronze 크롤러 그대로 | `description_curated` — `gyeol-brand-profile` 스킬 출력 |
| `name_eng` | `styles`, `name_ko` — 스킬이 채움 |

### `staging.product`

| raw (Bronze) | curated (후속 job) |
|---|---|
| `name`, `name_original`, `url`, `thumbnail_url`, `gallery_image_urls`, `description`, `original_price` | `category` — 크롤러 or `category_review` codex |
|  | `base_name`, `color` — regex or `base_name_review` codex |
|  | `sub_category`, `fit`, `description_curated`, `enrichment_meta` — Gemini |
|  | `embedding` — TEI |
|  | `search_text` — MeCab |
|  | `thumbnail_key`, `image_keys` — S3 uploader |

### `public.product` (서비스)

승격된 1급 컬럼 — `category`, `subcategory`, `sub_category_detail`, `color`, `fit`, `material[]`, `pattern`, `details[]`, `styles[]`.
`embedding vector(1024)` + `search_vector tsvector` (`simple(search_text)` GENERATED) 인덱스 탑재.

---

## Silver writer 철학 (재실행 안전)

`silver/writer.py` 의 UPSERT 는 **raw 컬럼만 덮어쓰고 curated 영역은 보존** 한다.

- brand: `description`, `name_eng`, `logo_url`, `url` 만 덮어씀. `description_curated`, `styles`, `name_ko` 는 건드리지 않음.
- product: `name`, `name_original`, `original_price`, `thumbnail_url`, `description`, `url` 은 덮어씀. `category`, `base_name`, `color` 는 `COALESCE(EXCLUDED, existing)` — Bronze 가 NULL 이면 기존 값 유지.

그래서 **silver 몇 번 재실행해도 LLM 이 채운 큐레이션 작업이 날아가지 않는다**.

---

## 운영 메모

- Codex 스킬 3 종은 `GYEOL_*_AUTOSTART` 환경변수 로 on/off. silver 가 trigger 시 `DB_URL` 을 subprocess env 로 전파.
- `codex` CLI 가 PATH 에 없으면 해당 스킬은 스킵만 하고 ETL 은 계속 진행.
- Gemini rate limit 에 맞춰 `GEMINI_MIN_INTERVAL_PER_KEY` (초) 로 페이싱. 429 가 터지면 이 값을 늘려야 함.
- Publish 는 `FOR UPDATE SKIP LOCKED` 로 배치 픽업 — 동시 여러 프로세스 돌려도 충돌 없음.

---

## 남은 TODO

- `dags/gyeol_publish.py` — publish 를 Airflow DAG 로 올리기 (schedule `*/5 * * * *`)
- Color vector (HSL 9d) 파이프라인
- 이미지 CloudFront + OAC 로 전환 (현재 S3 public-read)
- Backend API (`app/backend/`) 가 `public.product.embedding` 을 사용하는 검색 엔드포인트
