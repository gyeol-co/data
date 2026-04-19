-- Task queue — Airflow가 INSERT, codex-worker (Go) / Gemini worker가 pull + UPDATE.
-- Elice Community worker 패턴. status는 timestamp NULL 여부로 Computed.

CREATE SCHEMA IF NOT EXISTS orchestration;

CREATE TABLE orchestration.task (
    id              uuid PRIMARY KEY DEFAULT uuidv7(),
    task_type       text NOT NULL,
    scope_key       text NOT NULL,
    data            jsonb NOT NULL DEFAULT '{}',
    pid             int,
    attempt         int NOT NULL DEFAULT 0,
    result          jsonb,
    failed_reason   text,
    assigned        timestamptz,
    completed       timestamptz,
    failed          timestamptz,
    cancelled       timestamptz,
    status          text GENERATED ALWAYS AS (
        CASE
            WHEN failed    IS NOT NULL THEN 'failed'
            WHEN cancelled IS NOT NULL THEN 'cancelled'
            WHEN completed IS NOT NULL THEN 'completed'
            WHEN assigned  IS NOT NULL THEN 'assigned'
            ELSE                            'queued'
        END
    ) STORED,
    created         timestamptz NOT NULL DEFAULT now()
);

-- puller pick-up
CREATE INDEX idx_task_queued     ON orchestration.task (status, created)
    WHERE status = 'queued';

-- reconciler scan
CREATE INDEX idx_task_assigned   ON orchestration.task (status, assigned)
    WHERE status = 'assigned';

-- sensor lookup by scope
CREATE INDEX idx_task_type_scope ON orchestration.task (task_type, scope_key);
