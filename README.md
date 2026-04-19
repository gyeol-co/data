# 결 (Gyeol) — Data

> 패션 편집샵·브랜드샵의 상품을 긁어와 **검색/추천이 가능한 상태**까지 만드는 데이터 파이프라인.
> 크롤링 원본(Bronze) → 구조화(Silver) → LLM 큐레이션·임베딩(Gold) → 서비스 테이블(Public) 의 네 단계로 흐른다.

솔로 사이드 프로젝트로 만드는 취향 기반 패션 큐레이션 서비스 **결(Gyeol)** 의 데이터 백엔드.

---

## 지금 파이프라인에 태워둔 것

5 플랫폼 × 11 브랜드 × **1,888 상품** 기준:

| | 커버리지 |
|---|---:|
| `staging.product.category` (의류 타입) | 1,886 / 1,888 · 99.9% |
| `staging.product.base_name` · `color` (변형 정규화) | 1,868 / 1,847 |
| `staging.product.description_curated` (Gemini 큐레이션) | 1,886 |
| `staging.product.embedding` (bge-m3 1024d) | 1,886 |
| `staging.product.search_text` (MeCab 토큰) | 1,886 |
| `staging.product.thumbnail_key` / `image_keys` (S3) | 1,888 / 100% |
| **`public.product` (서비스 이관 완료)** | **1,874** |
| `public.brand` | 11 / 11 · 100% |

끝까지 안 간 14개는 codex 가 분류를 포기한 모호 케이스(워크셔츠 같기도, 재킷 같기도). 파이프라인이 그걸 "0 rows update" 가 아니라 **"stalled" 상태로 남기고** 나머지는 전진했다.

---

## 아키텍처 한 장

```
                                 ┌─────────────────┐
                                 │   brands.yaml   │  platform → brand → url
                                 └────────┬────────┘
                                          │
                    ┌─────────────────────▼─────────────────────┐
                    │  crawlers/                                  │
                    │    etcseoul · barbershop · bastong ·        │
                    │    sculpstore · modeman                     │
                    │    (httpx + playwright, 플랫폼마다 서브클래스)│
                    └─────────────────────┬─────────────────────┘
                                          │
                                    store/bronze/
                                  {platform}/{date}/
                                  {brand_id}.jsonl             [Bronze · 원본 불변]
                                          │
                    ┌─────────────────────▼─────────────────────┐
                    │  etl/silver/                              │
                    │    regex 정제 (상품명 · base_name · color)│
                    │    가격 검증, UPSERT                      │
                    │    └─▶ codex 3종 후속 job 트리거           │
                    │        (brand_profile / category_review /  │
                    │         base_name_review)                  │
                    └─────────────────────┬─────────────────────┘
                                          │
                                   staging.brand
                                   staging.product             [Silver · raw + curated 혼재]
                                          │
                    ┌─────────────────────▼─────────────────────┐
                    │  etl/gold/                                │
                    │    enrichment.py → Gemini 2.5-flash       │
                    │      sub_category · fit · material ·      │
                    │      pattern · details · style ·          │
                    │      description_curated                  │
                    │    embed.py → TEI(bge-m3) → vector(1024d) │
                    │    tokenize.py → MeCab NNG/NNP/SL/SN →    │
                    │      search_text                          │
                    └─────────────────────┬─────────────────────┘
                                          │
                              scripts/upload_images.py
                                 로컬/URL → S3 (public-read)
                                          │
                    ┌─────────────────────▼─────────────────────┐
                    │  etl/publish/                             │
                    │    publishable 조건 충족만 INSERT          │
                    │    (FOR UPDATE SKIP LOCKED, 멱등)          │
                    └─────────────────────┬─────────────────────┘
                                          │
                                   public.brand
                                   public.product               [Public · 서비스]
                                          │
                                          ▼
                                   → FastAPI 백엔드
                                     (pgvector hnsw +
                                      tsvector gin)
```

### 오케스트레이션

**Airflow 3.2.0** (custom image: Python 3.12 + MeCab + psycopg + pyyaml)

- `dags/_factory.py` — `pipelines/{platform}.yaml` 을 읽어 **플랫폼마다 DAG 하나씩 생성**. 같은 크롤러라도 sculpstore / modeman 은 `base_name_review` codex 스킬이 추가로 필요하므로, 매니페스트에 명시.
- `dags/gyeol_gold.py` — Gemini → embed → tokenize 체인. 단계마다 `ShortCircuitOperator` 로 pending=0 이면 건너뜀.
- `dags/gyeol_task_reconciler.py` — codex worker 가 죽어서 stuck 된 task 정리 (10분 간격).

---

## 핵심 설계 결정 (실제로 고민했던 것들)

### 1. Silver writer 는 Bronze raw 만 덮어쓰고 curated 영역은 **보존**한다

크롤러가 가져온 원본(`description`, `original_price` 등) 은 매번 최신으로 덮어써야 맞지만,
codex 가 채운 `description_curated`, `category`(regex 로 못 뽑는 플랫폼), `styles`, `name_ko` 는
silver 재실행마다 날아가면 안 된다. UPSERT 문에서 **SET 절 자체에서 curated 컬럼을 제외**하거나
`COALESCE(EXCLUDED.x, existing.x)` 로 처리.

> 이 버그를 초기에 한 번 맞았고 (silver 한 번 더 돌렸더니 brand description 3개가 NULL 로 리셋),
> `staging.brand` 에 `description_curated` 컬럼을 추가해서 `staging.product.description` / `description_curated`
> 분리 패턴을 그대로 따라갔다.

### 2. 플랫폼마다 필요한 ETL 단계가 다르다 → 매니페스트로 선언

- `etcseoul`, `barbershop`, `bastong` — regex 로 base_name/color 추출 가능
- `sculpstore`, `modeman` — 상품명이 불규칙 → **codex `base_name_review` 가 꼭 필요**

하드코딩 대신 `pipelines/{platform}.yaml` 에서 `codex_skills: [...]` 선언.
DAG factory 가 이걸 읽어 플랫폼별 DAG 를 따로 만든다. 플랫폼 추가 = YAML 한 개 추가.

### 3. codex async job 은 fire-and-forget 이 아니라 **큐 + reconciler** 패턴

초기엔 Silver 가 `subprocess.Popen(..., stdout=DEVNULL)` 로 codex 를 띄우고 끝나버렸는데 —
실패하든 성공하든 추적 불가. Elice Community 워커의 Task 테이블 패턴을 참고해
`orchestration.task` 에 Computed status 컬럼을 둔 큐를 깔고, DAG 는 task 를 enqueue + SqlSensor 로
완료만 기다리는 구조로 바꿨다.

### 4. Gemini rate limit — 6개 키 round-robin + 키별 스로틀

무지성 20 worker 로 쏘면 key 하나당 RPM 초과로 429 폭주 → 각 429 마다 10s 백오프
때문에 **오히려 전체 throughput 이 떨어진다**.

```python
# etl/gold/enrichment.py
with _key_lock:
    key = _keys[_key_index % len(_keys)]
    _key_index += 1
    last = _key_last_call.get(key, 0)
    wait = GEMINI_MIN_INTERVAL_PER_KEY - (now - last)
    _key_last_call[key] = max(now, last + GEMINI_MIN_INTERVAL_PER_KEY)  # 예약
# 락 밖에서 sleep (다른 스레드 블록 X)
if wait > 0: time.sleep(wait)
```

키당 0.5s 간격 = 6 keys × 120 RPM = **720 RPM 상한**. 안정적으로 분당 ~60 건 처리.

### 5. MeCab pre-tokenize + Postgres `to_tsvector('simple')`

한국어 BM25 를 위해 Postgres 에 MeCab 확장을 깔지 않고 (운영 복잡도 ↑) **Python 측에서 MeCab 돌려
공백 구분 토큰을 `search_text` 에 저장 → Postgres 는 `simple` tokenizer 로 인덱싱** 하는 패턴.

선택 품사: `NNG` (일반명사) `NNP` (고유명사) `SL` (외국어) `SN` (숫자).
패션 쿼리는 거의 명사 기반 (`"워크웨어 재킷"`, `"90s 빈티지"`) 이라 이걸로 충분.
`VA` 형용사 같은 건 노이즈라서 제외.

### 6. S3 는 public bucket policy (prefix 한정) + 이미지 URL 직행

상품 이미지는 본질적으로 공개 콘텐츠 → Presigned URL 은 캐시가 깨져 비용↑, 복잡도↑.
`s3:GetObject` 를 `products/*` prefix 에만 풀고, 프론트엔드는
`https://{bucket}.s3.{region}.amazonaws.com/{thumbnail_key}` 로 바로 접근.

로컬에 이미지 캐시하다 staging UUID 세대 교체 시 **고아 이미지 5GB** 가 생기는 걸 경험한 뒤,
upload 스크립트를 **로컬 없으면 URL 에서 곧장 S3 스트리밍** 으로 바꿔 디스크 사용량을 0 에 가깝게.

### 7. Publish 는 "계속 도는 cron 성 데몬" — 실패 트래킹 안 한다

`staging.product.published timestamptz` NULL 인 publishable row 만 픽업 → 트랜잭션 INSERT + UPDATE.
실패 트래킹 컬럼(`publish_failed_at`) 은 **일부러 안 만들었다**.

> publishable index 조건이 이미 빡빡해서(모든 NOT NULL 컬럼 + vectorized), 실제로 row 레벨 실패가
> 날 여지가 거의 없다. 실패한다면 infra 문제 → 다음 run 에 자연 복구. 정말 데이터 버그면 로그가
> 시끄러워질 뿐이다. YAGNI.

---

## 기술 스택

| 레이어 | 선택 | 비고 |
|---|---|---|
| 언어 | Python 3.12 | 데이터 작업은 전부 Python |
| DB | Postgres 16 + **pgvector** + **timescaledb** + **tsvector** | 하나의 DB 에 다 실어놨다. 이벤트는 hypertable |
| Crawler | httpx + BeautifulSoup + Playwright(필요시) | 비동기, 공통 `BaseCrawler` 상속 |
| Silver | psycopg3, 순수 SQL UPSERT | ORM 미사용 — 속도 + 투명성 |
| Gemini | `gemini-2.5-flash` 멀티모달 (이미지 10장까지) + Pydantic 구조화 validation | Taxonomy enum 과 cross-field 검증 |
| Embedding | HF **Text Embeddings Inference (TEI)** / `BAAI/bge-m3` 1024d | Docker 컨테이너, GPU |
| 토크나이저 | **python-mecab-ko** | Airflow custom image 에 `mecab-ko` + dict 빌드 포함 |
| 스토리지 | AWS S3 (ap-southeast-2) | public bucket policy on `products/*` |
| 오케스트레이션 | **Airflow 3.2.0** LocalExecutor | scheduler + dag-processor + triggerer + api-server + 별도 meta postgres |
| Codex 스킬 | `codex exec --dangerously-bypass-approvals-and-sandbox -o last_message.txt` | stdout/stderr 는 파일, 상태는 `status.txt` + Postgres 큐 |
| Migration | golang-migrate (raw SQL) | alembic 미사용 |

---

## 디렉토리

```
.
├── brands.yaml                   # 크롤 대상 (platform → brand_id → url)
├── common/                       # Pydantic schema (CrawlResult, RawProduct, RawBrand) + taxonomy enum
├── crawlers/                     # 플랫폼별 crawler (BaseCrawler 상속)
├── etl/
│   ├── silver/                   # Bronze → staging
│   ├── gold/                     # Gemini + embedding + MeCab tokenize
│   └── publish/                  # staging → public 이관 데몬
├── scripts/                      # upload_images, download_images, category_labels 등
├── migrations/                   # 001-008 SQL
├── pipelines/                    # 플랫폼별 ETL 매니페스트 (YAML)
├── dags/                         # Airflow DAG (factory + gold + reconciler)
├── orchestration/                # DAG 에서 부를 얇은 함수
├── workers/codex/                # codex 통합 executor spec (Go 로 옮길 계획)
├── skills/                       # 로컬 codex 스킬
├── docs/architecture_overview.md # 전체 시스템 큰 그림
├── docker-compose.yml            # postgres / TEI embedding / reranker / Airflow 5-service
└── Dockerfile.airflow            # MeCab 포함 custom image
```

---

## 실행

```bash
# 1. 인프라
docker compose up -d postgres embedding airflow-init
docker compose up -d airflow-scheduler airflow-dag-processor airflow-triggerer airflow-api-server

# 2. 마이그레이션
make migrate-up

# 3. 의존성
uv sync

# 4. 전체 파이프라인 수동 실행
uv run python -m crawlers.run                      # crawl → Bronze
uv run python -m etl.silver.run                    # Bronze → staging + codex 트리거
uv run python main.py gold --workers 20            # Gemini enrichment
uv run python scripts/upload_images.py             # → S3
docker exec airflow-scheduler python -c \
  "from etl.gold.tokenize import tokenize_all; print(tokenize_all())"
uv run python -m etl.publish                       # staging → public
```

Airflow UI: `http://localhost:8082`

---

## 문서

- [docs/architecture_overview.md](docs/architecture_overview.md) — 전체 시스템 구조 (data / serving / event 레이어)
- [select_shops.md](select_shops.md) — 편집샵/브랜드샵 선별 기준

---

## 의도적으로 안 한 것

- **마이크로서비스화** — 솔로 pre-launch, 단일 Postgres 에 모든 테이블. 확장 필요할 때 스키마 분리.
- **Celery/KubernetesExecutor** — LocalExecutor 로 충분. 유저 0, 트래픽 0.
- **Publish failed_at 컬럼** — YAGNI (위 설계 결정 7 참고).
- **Qdrant / Weaviate** — pgvector 로 먼저. 수십만 건 되면 분리 고려.
- **CloudFront** — S3 public direct 로 시작. 이미지 traffic 이 유의미해질 때 전환.

---

## 남은 로드맵

- Publish DAG 를 `*/5 * * * *` cron 으로 올리기 (지금은 수동 or `--watch`)
- Color vector (HSL 9d) — 톤 매칭 검색용
- 이벤트 파이프라인 (Kafka → ClickHouse → 학습데이터)
- 추천 모델 재학습 DAG (주 1회)
