-- Legacy SQLite collaborators table, kept ONLY for the role-readers test double
-- (infra/tests/tasks/role_readers_task_store_test.py).
-- The SQLite control plane was retired on 2026-10-11; its schema lives in git history only.

CREATE TABLE collaborators (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT UNIQUE,
    role TEXT DEFAULT 'member' CHECK(role IN ('owner', 'admin', 'member', 'viewer')),
    avatar_url TEXT,
    is_active BOOLEAN DEFAULT 1,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
