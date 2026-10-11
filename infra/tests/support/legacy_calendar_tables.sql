-- Legacy SQLite calendar table, kept ONLY for test doubles that still read it
-- (task_calendar_health via infra/tests/integrations/calendar_task_sync_test.py).
-- The SQLite control plane was retired on 2026-10-11; its schema lives in git history only.

CREATE TABLE calendar_events (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL DEFAULT 'google',
    title TEXT NOT NULL,
    description TEXT,
    start_time DATETIME NOT NULL,
    end_time DATETIME NOT NULL,
    location TEXT,
    attendees TEXT,
    is_all_day INTEGER DEFAULT 0,
    recurrence_rule TEXT,
    color TEXT,
    status TEXT DEFAULT 'confirmed',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
