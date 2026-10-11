-- Legacy SQLite notification tables (retired 2026-10-10, notifications live in the solo-notifications cell).
-- Tests apply this DDL to a temporary database for SqliteNotificationsStore, production never does.

CREATE TABLE IF NOT EXISTS notification_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT,
    rule_type TEXT NOT NULL CHECK(rule_type IN (
        'overdue', /* Task past due_date */
        'due_soon', /* Task due within N hours */
        'high_priority', /* Priority 1-2 tasks */
        'stale', /* No updates for N days */
        'blocked', /* Tasks in blocked status */
        'status_change', /* Task status changed */
        'reminder' /* Manual reminder_at reached */
    )),
    -- Rule configuration (JSON)
    -- Examples:
    --   overdue: {}
    --   due_soon: {"hours": 24}
    --   high_priority: {"max_priority": 2}
    --   stale: {"days": 7}
    --   blocked: {}
    --   reminder: {}
    config TEXT DEFAULT '{}',
    -- Channels to notify: JSON array ["console", "telegram", "email", "desktop"]
    channels TEXT DEFAULT '["console"]',
    -- Filtering
    project_filter TEXT,
    -- NULL = all projects, or specific project name
    category_filter TEXT,
    -- NULL = all categories
    priority_filter INTEGER,
    -- NULL = all, or max priority to include
    status_filter TEXT,
    -- JSON array of statuses to include, NULL = active only

    -- Scheduling
    check_interval_minutes INTEGER DEFAULT 60,
    -- How often to check this rule
    last_checked_at DATETIME,
    last_triggered_at DATETIME,
    -- State
    is_active BOOLEAN DEFAULT 1,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS notification_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id INTEGER REFERENCES notification_rules(id),
    task_id INTEGER,
    -- Notification content
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    notification_type TEXT NOT NULL,
    -- matches rule_type
    severity TEXT DEFAULT 'info' CHECK(severity IN ('info', 'warning', 'critical', 'success')),
    -- Channel used
    channel TEXT NOT NULL DEFAULT 'console',
    -- Task snapshot at notification time
    task_title TEXT,
    task_status TEXT,
    task_priority INTEGER,
    task_due_date TEXT,
    task_project TEXT,
    -- Delivery tracking
    sent_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    read_at DATETIME,
    dismissed_at DATETIME,
    -- Metadata
    metadata TEXT /* JSON for extra data */,
    delivered_at DATETIME,
    delivery_channel TEXT,
    delivery_error TEXT,
    delivery_attempts INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL,
    channel TEXT NOT NULL,
    -- console, line, slack, email
    message TEXT NOT NULL,
    sent_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    is_read INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_notif_rules_active ON notification_rules(is_active);

CREATE INDEX IF NOT EXISTS idx_notif_rules_type ON notification_rules(rule_type);

CREATE INDEX IF NOT EXISTS idx_notif_log_rule ON notification_log(rule_id);

CREATE INDEX IF NOT EXISTS idx_notif_log_task ON notification_log(task_id);

CREATE INDEX IF NOT EXISTS idx_notif_log_sent ON notification_log(sent_at DESC);

CREATE INDEX IF NOT EXISTS idx_notif_log_unread ON notification_log(read_at) WHERE read_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_notification_log_delivery
    ON notification_log (notification_type, delivered_at, id);

CREATE INDEX IF NOT EXISTS idx_notif_task ON notifications(task_id);

CREATE INDEX IF NOT EXISTS idx_notif_sent ON notifications(sent_at DESC);

CREATE INDEX IF NOT EXISTS idx_notif_unread ON notifications(is_read) WHERE is_read = 0;
