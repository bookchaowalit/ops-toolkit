-- The retired operating, intake and automation tables, for the SQLite test doubles only.
-- Production reads and writes them through the cell APIs (infra/scripts/utils/cell_store.py).

CREATE TABLE IF NOT EXISTS source_registry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_key TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    source_kind TEXT NOT NULL
        CHECK (source_kind IN ('manual', 'file', 'api', 'webhook', 'email', 'calendar', 'database', 'application', 'other')),
    provider TEXT,
    domain TEXT NOT NULL,
    dataset TEXT NOT NULL,
    owner_scope TEXT NOT NULL DEFAULT 'solo_empire'
        CHECK (owner_scope IN ('personal', 'solo_empire', 'venture', 'client', 'shared')),
    privacy_class TEXT NOT NULL DEFAULT 'private'
        CHECK (privacy_class IN ('public', 'internal', 'private', 'restricted')),
    retention_class TEXT NOT NULL DEFAULT 'operational'
        CHECK (length(trim(retention_class)) BETWEEN 1 AND 64),
    ingest_mode TEXT NOT NULL DEFAULT 'manual'
        CHECK (ingest_mode IN ('manual', 'batch', 'stream', 'webhook')),
    status TEXT NOT NULL DEFAULT 'planned'
        CHECK (status IN ('planned', 'active', 'paused', 'retired')),
    description TEXT,
    last_ingested_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    CHECK (length(trim(source_key)) BETWEEN 1 AND 64),
    CHECK (length(trim(display_name)) BETWEEN 1 AND 160),
    CHECK (length(trim(domain)) BETWEEN 1 AND 80),
    CHECK (length(trim(dataset)) BETWEEN 1 AND 120)
);

CREATE TABLE IF NOT EXISTS intake_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    intake_key TEXT NOT NULL UNIQUE,
    source_id INTEGER NOT NULL REFERENCES source_registry(id) ON DELETE RESTRICT,
    domain TEXT NOT NULL,
    dataset TEXT NOT NULL,
    schema_version TEXT NOT NULL DEFAULT '1',
    payload_kind TEXT NOT NULL
        CHECK (payload_kind IN ('json', 'jsonl', 'csv', 'binary', 'markdown', 'parquet', 'other')),
    input_ref TEXT NOT NULL,
    source_batch_ref TEXT,
    idempotency_key TEXT NOT NULL UNIQUE,
    privacy_class TEXT NOT NULL DEFAULT 'private'
        CHECK (privacy_class IN ('public', 'internal', 'private', 'restricted')),
    retention_class TEXT NOT NULL DEFAULT 'operational'
        CHECK (length(trim(retention_class)) BETWEEN 1 AND 64),
    requested_by TEXT NOT NULL DEFAULT 'human'
        CHECK (requested_by IN ('human', 'agent', 'automation')),
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'processing', 'landed', 'verified', 'rejected', 'failed', 'cancelled')),
    requested_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    claimed_at DATETIME,
    lease_owner TEXT,
    lease_expires_at DATETIME,
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    record_count INTEGER CHECK (record_count IS NULL OR record_count >= 0),
    raw_sha256 TEXT
        CHECK (raw_sha256 IS NULL OR (length(raw_sha256) = 64 AND raw_sha256 NOT GLOB '*[^0-9a-fA-F]*')),
    lake_run_id TEXT,
    lake_manifest_key TEXT,
    lake_raw_object_key TEXT,
    lake_bronze_object_key TEXT,
    error_code TEXT,
    error_message TEXT,
    verified_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    CHECK (length(trim(intake_key)) BETWEEN 1 AND 96),
    CHECK (length(trim(domain)) BETWEEN 1 AND 80),
    CHECK (length(trim(dataset)) BETWEEN 1 AND 120),
    CHECK (length(trim(schema_version)) BETWEEN 1 AND 32),
    CHECK (length(trim(input_ref)) BETWEEN 1 AND 240),
    CHECK (source_batch_ref IS NULL OR length(trim(source_batch_ref)) BETWEEN 1 AND 160),
    CHECK (length(trim(idempotency_key)) BETWEEN 1 AND 160),
    CHECK (lake_manifest_key IS NULL OR length(trim(lake_manifest_key)) BETWEEN 1 AND 512),
    CHECK (lake_raw_object_key IS NULL OR length(trim(lake_raw_object_key)) BETWEEN 1 AND 512),
    CHECK (lake_bronze_object_key IS NULL OR length(trim(lake_bronze_object_key)) BETWEEN 1 AND 512),
    CHECK (error_code IS NULL OR length(trim(error_code)) BETWEEN 1 AND 80),
    CHECK (error_message IS NULL OR length(error_message) <= 500)
);

CREATE TABLE IF NOT EXISTS intake_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    intake_id INTEGER NOT NULL REFERENCES intake_queue(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL
        CHECK (event_type IN ('queued', 'claimed', 'lease_renewed', 'landed', 'verified', 'rejected', 'failed', 'retried', 'cancelled')),
    actor TEXT NOT NULL DEFAULT 'system',
    from_status TEXT,
    to_status TEXT NOT NULL,
    reason TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    CHECK (length(trim(actor)) BETWEEN 1 AND 80),
    CHECK (length(trim(to_status)) BETWEEN 1 AND 32),
    CHECK (reason IS NULL OR length(reason) <= 500)
);

CREATE TABLE IF NOT EXISTS operating_cycles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    cycle_type TEXT NOT NULL DEFAULT 'sprint'
        CHECK(cycle_type IN ('sprint', 'weekly', 'planning', 'review')),
    status TEXT NOT NULL DEFAULT 'planned'
        CHECK(status IN ('planned', 'active', 'in_review', 'closed', 'cancelled')),
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    focus TEXT NOT NULL,
    capacity_minutes INTEGER CHECK(capacity_minutes IS NULL OR capacity_minutes >= 0),
    owner TEXT NOT NULL DEFAULT 'Owner',
    decision TEXT
        CHECK(decision IS NULL OR decision IN ('continue', 'adjust', 'pause', 'close')),
    source TEXT NOT NULL DEFAULT 'ai-operating-cadence',
    notes TEXT,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK(end_date >= start_date)
);

CREATE TABLE IF NOT EXISTS operating_cycle_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id INTEGER NOT NULL REFERENCES "operating_cycles"(id) ON DELETE CASCADE,
    task_id INTEGER NOT NULL,
    commitment_type TEXT NOT NULL DEFAULT 'committed'
        CHECK(commitment_type IN ('committed', 'stretch', 'carried_over', 'dropped')),
    estimate_minutes INTEGER CHECK(estimate_minutes IS NULL OR estimate_minutes >= 0),
    evidence_required INTEGER NOT NULL DEFAULT 1 CHECK(evidence_required IN (0, 1)),
    notes TEXT,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(cycle_id, task_id)
);

CREATE TABLE IF NOT EXISTS operating_cycle_goal_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id INTEGER NOT NULL REFERENCES "operating_cycles"(id) ON DELETE CASCADE,
    goal_id INTEGER NOT NULL,
    link_type TEXT NOT NULL DEFAULT 'objective'
        CHECK(link_type IN ('objective', 'supporting', 'guardrail')),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(cycle_id, goal_id)
);

CREATE TABLE IF NOT EXISTS operating_cycle_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id INTEGER NOT NULL UNIQUE REFERENCES "operating_cycles"(id) ON DELETE CASCADE,
    reviewer TEXT NOT NULL DEFAULT 'Owner',
    summary TEXT NOT NULL,
    delivered_summary TEXT,
    carryover_summary TEXT,
    blocker_summary TEXT,
    learning_summary TEXT,
    decision TEXT NOT NULL
        CHECK(decision IN ('continue', 'adjust', 'pause', 'close')),
    evidence_ref TEXT,
    reviewed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS operating_kpi_definitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    unit TEXT NOT NULL DEFAULT 'count',
    direction TEXT NOT NULL DEFAULT 'increase'
        CHECK(direction IN ('increase', 'decrease', 'maintain')),
    target_value REAL NOT NULL,
    source_kind TEXT NOT NULL
        CHECK(source_kind IN ('cycle_tasks', 'ai_telemetry', 'review', 'manual')),
    privacy_class TEXT NOT NULL DEFAULT 'internal'
        CHECK(privacy_class IN ('public', 'internal', 'private')),
    metric_group TEXT NOT NULL DEFAULT 'operating',
    measurement_cadence TEXT NOT NULL DEFAULT 'weekly',
    status TEXT NOT NULL DEFAULT 'active'
        CHECK(status IN ('active', 'paused', 'retired')),
    notes TEXT,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS operating_kpi_measurements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kpi_id INTEGER NOT NULL REFERENCES "operating_kpi_definitions"(id) ON DELETE CASCADE,
    cycle_id INTEGER NOT NULL REFERENCES "operating_cycles"(id) ON DELETE CASCADE,
    value REAL NOT NULL,
    target_value REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'no_data'
        CHECK(status IN ('no_data', 'on_track', 'at_risk', 'off_track', 'achieved')),
    measured_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    source_ref TEXT,
    evidence_ref TEXT,
    metadata_json TEXT,
    UNIQUE(kpi_id, cycle_id)
);

CREATE TABLE IF NOT EXISTS cron_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT,
    schedule TEXT NOT NULL,
    job_type TEXT NOT NULL CHECK(job_type IN (
        'sync', 'smart_sync', 'generate_tasks', 'check_reminders',
        'check_notifications', 'engagement_notifications', 'take_snapshot',
        'auto_archive', 'run_pipeline', 'update_goals', 'revenue_autorun',
        'weekly_habit_autogranted', 'weekly_review_gen', 'goal_progress',
        'db_health_check', 'system_status', 'domain_scanner', 'run_alerts',
        'sync_calendar', 'sync_gmail', 'sync_leantime', 'sync_strava',
        'sync_binance', 'sync_all', 'refresh_dashboards', 'backup_db',
        'analytics_dashboard', 'revenue_analytics', 'habit_analytics',
        'goal_velocity', 'time_analytics', 'cron_heal', 'data_freshness',
        'gmail_categorize', 'gmail_to_todoist', 'gmail_daily_digest',
        'gmail_filters', 'reconcile_overdue', 'insights', 'generate_pages',
        'publish_pages', 'media_lifecycle', 'decision_review',
        'decision_brief_daily', 'decision_brief_weekly', 'daily_diary',
        'book_dev_loop', 'brave_bookmarks_maintenance',
        'diary_weekly_summary', 'diary_monthly_summary',
        'sales_pipeline', 'finance_daily_review', 'finance_daily_notification',
        'job_data_readiness'
    )),
    params TEXT DEFAULT '{}',
    is_active BOOLEAN DEFAULT 1,
    last_run_at DATETIME,
    next_run_at DATETIME,
    last_status TEXT DEFAULT 'pending',
    retry_count INTEGER NOT NULL DEFAULT 0,
    last_retry_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS automation_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT,
    -- Trigger condition (JSON)
    -- Examples:
    --   {"type": "status_change", "from": "todo", "to": "in_progress"}
    --   {"type": "due_date_approaching", "hours": 24}
    --   {"type": "priority_changed", "to": 1}
    --   {"type": "task_created", "project": "DCS"}
    --   {"type": "task_completed", "project": null}
    trigger TEXT NOT NULL,
    -- Action to take (JSON)
    -- Examples:
    --   {"type": "update_status", "status": "in_progress"}
    --   {"type": "add_tag", "tag": "urgent"}
    --   {"type": "send_notification", "message": "Task is due soon"}
    --   {"type": "create_subtask", "title": "Review: {parent_title}"}
    --   {"type": "move_project", "project": "Review"}
    action TEXT NOT NULL,
    -- Scope
    project_filter TEXT,
    -- NULL = all projects
    priority_filter INTEGER,
    -- NULL = all priorities
    category_filter TEXT,
    -- NULL = all categories
    
    -- State
    is_active BOOLEAN DEFAULT 1,
    execution_count INTEGER DEFAULT 0,
    last_executed_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS workflow_pipelines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT,
    -- Steps (JSON array of actions)
    -- [
    --   {"step": 1, "action": "filter", "criteria": {"status": "todo", "project": "DCS"}},
    --   {"step": 2, "action": "update_status", "status": "in_progress"},
    --   {"step": 3, "action": "add_tag", "tag": "batch-processed"}
    -- ]
    steps TEXT NOT NULL,
    -- State
    is_active BOOLEAN DEFAULT 1,
    run_count INTEGER DEFAULT 0,
    last_run_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS automation_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id INTEGER REFERENCES automation_rules(id),
    pipeline_id INTEGER REFERENCES workflow_pipelines(id),
    -- What happened
    action_type TEXT NOT NULL,
    -- 'rule', 'pipeline', 'cron'
    description TEXT NOT NULL,
    -- Affected tasks (JSON array of task IDs)
    affected_tasks TEXT,
    affected_count INTEGER DEFAULT 0,
    -- Result
    success BOOLEAN DEFAULT 1,
    error_message TEXT,
    -- Metadata
    executed_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_auto_rules_active ON automation_rules(is_active);
CREATE INDEX IF NOT EXISTS idx_auto_log_rule ON automation_log(rule_id);
CREATE INDEX IF NOT EXISTS idx_auto_log_exec ON automation_log(executed_at DESC);
CREATE INDEX IF NOT EXISTS idx_source_registry_status
    ON source_registry(status, domain, dataset);
CREATE INDEX IF NOT EXISTS idx_source_registry_privacy
    ON source_registry(privacy_class, owner_scope);
CREATE INDEX IF NOT EXISTS idx_intake_queue_status
    ON intake_queue(status, requested_at);
CREATE INDEX IF NOT EXISTS idx_intake_queue_source
    ON intake_queue(source_id, status, requested_at);
CREATE INDEX IF NOT EXISTS idx_intake_queue_lease
    ON intake_queue(status, lease_expires_at);
CREATE INDEX IF NOT EXISTS idx_operating_cycles_status_dates
    ON operating_cycles(status, start_date, end_date);
CREATE INDEX IF NOT EXISTS idx_operating_kpi_measurements_cycle
    ON operating_kpi_measurements(cycle_id, measured_at DESC);
CREATE INDEX IF NOT EXISTS idx_intake_events_intake
    ON intake_events(intake_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_cron_active ON cron_jobs(is_active);
CREATE INDEX IF NOT EXISTS idx_cron_next ON cron_jobs(next_run_at);
CREATE INDEX IF NOT EXISTS idx_operating_cycle_tasks_task
    ON operating_cycle_tasks(task_id, cycle_id);
CREATE INDEX IF NOT EXISTS idx_operating_cycle_goal_links_goal
    ON operating_cycle_goal_links(goal_id, cycle_id);
