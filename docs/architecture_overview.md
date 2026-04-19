# Gyeol Architecture Overview

## System Components

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          Trigger Layer                                   │
│                                                                          │
│            [Scheduler]        [Event]          [Rule]                    │
│            Airflow Cron       신상품 감지        수동 트리거               │
│                │                 │                │                      │
│                └─────────┬───────┘────────────────┘                      │
│                          ↓                                               │
└──────────────────────────┼──────────────────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────────────────────┐
│                     ETL Instance (Offline)                                │
│                                                                          │
│   ┌──────────┐    ┌──────────────┐    ┌─────────────┐    ┌──────────┐  │
│   │ Crawler  │───→│ Data Pipeline│───→│  Embedding  │───→│  Color   │  │
│   │          │    │              │    │   Model     │    │ Palette  │  │
│   │ 5 platforms   │ Bronze→Silver│    │ GTE 305M    │    │ rembg    │  │
│   │ 11 brands│    │ →Gold        │    │ dense 1024d │    │ HSL 9d   │  │
│   └────┬─────┘    └──────┬───────┘    └──────┬──────┘    └────┬─────┘  │
│        │                 │                   │                │         │
│   Platforms,        Gemini 2.5 Flash         │                │         │
│   Select Shops,     MeCab 형태소 분석         │                │         │
│   Brands                 │                   │                │         │
│                          ↓                   ↓                ↓         │
│                    ┌──────────┐         ┌─────────┐                     │
│                    │PostgreSQL│         │ Qdrant  │                     │
│                    │  + BM25  │         │dense+color                    │
│                    └──────────┘         └─────────┘                     │
└─────────────────────────────────────────────────────────────────────────┘


┌─────────────────────────────────────────────────────────────────────────┐
│                   Web Service Instance (Online)                          │
│                                                                          │
│   Users                                                                  │
│     │                                                                    │
│     ├── Text Query ──→ [Embedding Model] ──→ Vector Query                │
│     │                   GTE 305M (~20ms)                                 │
│     │                                                                    │
│     ├── Browse ──→ SQL WHERE (category, style, fit, brand, price)        │
│     │                                                                    │
│     └── Click/View ──→ [Event Collector] ──→ Kafka                       │
│                                                                          │
│                                                                          │
│   ┌──────────────────── Retrieval ────────────────────┐                  │
│   │                                                    │                 │
│   │  Search:     PG BM25 top-50 ──┐                    │                 │
│   │              Qdrant dense top-50 ─┼──→ RRF 합산     │                 │
│   │                                                    │                 │
│   │  Recommend:  Qdrant dense similarity top-50        │                 │
│   │  Color:      Qdrant color similarity top-50        │                 │
│   │  Home Feed:  Redis GET (pre-computed)              │                 │
│   │                                                    │                 │
│   └───────────────────────┬────────────────────────────┘                 │
│                           ↓                                              │
│   ┌──────────────────── Re-ranking ───────────────────┐                  │
│   │                                                    │                 │
│   │  Redis GET → user features + item features         │                 │
│   │  Model Server /rank → 클릭 확률 예측                │                 │
│   │  Top-K 선택 + 응답                                  │                 │
│   │                                                    │                 │
│   └───────────────────────┬────────────────────────────┘                 │
│                           ↓                                              │
│                   Top K Results → Users                                  │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘


┌─────────────────────────────────────────────────────────────────────────┐
│                    Event & Feedback Pipeline                              │
│                                                                          │
│   [Kafka]                                                                │
│     │                                                                    │
│     ├──→ [ClickHouse]    이벤트 저장, 분석 대시보드, 학습 데이터            │
│     │                                                                    │
│     └──→ [Airflow DAGs]                                                  │
│            │                                                             │
│            ├── DAG: Feature Aggregation (매시간)                           │
│            │    ClickHouse → 유저/아이템 피처 집계 → Redis                  │
│            │                                                             │
│            ├── DAG: Pre-compute Recommendations (매일)                    │
│            │    유저별 → Qdrant top-200 → Model Server re-rank            │
│            │    → Redis user:{id}:feed                                   │
│            │                                                             │
│            ├── DAG: Similar Products (신상품 등록 시)                      │
│            │    상품별 → Qdrant top-30 → Redis product:{id}:similar       │
│            │                                                             │
│            └── DAG: Model Retrain (주 1회)                                │
│                 ClickHouse → 학습 데이터 추출 → 모델 학습                   │
│                 → Model Server 배포                                      │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Data Stores

```
┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│  PostgreSQL  │  │    Qdrant    │  │    Redis     │  │  ClickHouse  │
│              │  │              │  │              │  │              │
│ Source of    │  │ Vector       │  │ Cache +      │  │ Event        │
│ Truth        │  │ Search       │  │ Feature Store│  │ Analytics    │
│              │  │              │  │              │  │              │
│ products     │  │ dense        │  │ pre-computed │  │ impressions  │
│ brands       │  │  [1024d]     │  │  feed        │  │ clicks       │
│ users        │  │              │  │  similar     │  │ searches     │
│ BM25 index   │  │ color        │  │              │  │ purchases    │
│  (tsvector)  │  │  [9d]        │  │ features     │  │              │
│              │  │              │  │  user:{}     │  │ 학습 데이터   │
│              │  │              │  │  item:{}     │  │ 대시보드      │
└──────────────┘  └──────────────┘  └──────────────┘  └──────────────┘
    정합성             유사도             속도              분석
   ACID 보장        ANN 검색           ~1ms 읽기       대량 집계 최적화
```

---

## Model Serving

```
┌─────────────────────────────────────────────┐
│          Model Serving Server                │
│          (Triton / BentoML)                  │
│                                              │
│   /embed                                     │
│     입력: 텍스트 or 이미지                     │
│     출력: 1024d dense vector                  │
│     모델: GTE-Multilingual-Base (305M)        │
│     용도: 검색 쿼리 임베딩, 신상품 임베딩        │
│                                              │
│   /rank                                      │
│     입력: [(user_features, item_features)]    │
│     출력: [click_probability]                 │
│     모델: LightGBM / MLP                     │
│     용도: 추천 결과 재정렬                      │
│                                              │
│   /color                                     │
│     입력: 상품 이미지                          │
│     출력: 9d HSL color vector                 │
│     모델: rembg + k-means                    │
│     용도: 색감 매칭용 벡터 생성                  │
│                                              │
└─────────────────────────────────────────────┘
```

---

## Request Flow Examples

### Home Feed (pre-computed)

```
User → API
         → Redis GET user:{id}:feed           [~1ms]
         → Redis MGET user:feature:{id}       [~1ms]
         → Redis MGET item:feature:{id}...    [~2ms]
         → Model Server /rank (re-rank top-50) [~10ms]
         → Top-20 응답
         → Kafka: impression events 발행
```

### Search (real-time)

```
User "워크웨어 재킷" → API
         → Model Server /embed (쿼리 임베딩)    [~20ms]
         → PostgreSQL BM25 top-50              [~5ms]  ┐
         → Qdrant Dense top-50                 [~20ms] ┤ 병렬
         → RRF 합산 → 후보 ~70개                        ┘
         → Redis: user/item features 조회       [~3ms]
         → Model Server /rank (re-rank)        [~10ms]
         → Top-20 응답
         → Kafka: search-query + impressions 발행
```

### Similar Products (hybrid)

```
User (상품 상세) → API
         → Redis GET product:{id}:similar      [~1ms, pre-computed]
         → Redis: user features 조회            [~1ms]
         → Model Server /rank (개인화 re-rank)  [~10ms]
         → Top-6 응답
         → Kafka: impression events 발행
```

### Color Matching (real-time)

```
User (톤 매칭) → API
         → Qdrant color vector similarity top-50  [~15ms]
         → Redis: user/item features 조회          [~3ms]
         → Model Server /rank (re-rank)            [~10ms]
         → Top-20 응답
         → Kafka: impression events 발행
```
