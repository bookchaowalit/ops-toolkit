-- Legacy SQLite habit tables (retired 2026-10-10, habits live in the solo-habits cell).
-- Tests apply this DDL to a temporary database for SqliteHabitsStore, production never does.

CREATE TABLE IF NOT EXISTS habits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    description TEXT,
    -- Classification
    category TEXT CHECK(category IN ('health', 'learning', 'business', 'creative', 'mindfulness', 'productivity', 'social', 'financial')) DEFAULT 'productivity',
    -- Target
    frequency TEXT CHECK(frequency IN ('daily', 'weekly', 'monthly')) DEFAULT 'daily',
    target_count INTEGER DEFAULT 1,
    -- times per frequency
    target_duration_minutes INTEGER,
    -- optional time target

    -- Tracking
    status TEXT CHECK(status IN ('active', 'paused', 'completed', 'abandoned')) DEFAULT 'active',
    priority INTEGER DEFAULT 3,
    -- Streak
    current_streak INTEGER DEFAULT 0,
    best_streak INTEGER DEFAULT 0,
    last_completed_at DATE,
    -- Schedule
    schedule_days TEXT,
    -- JSON array: ["mon","tue","wed","thu","fri"] for weekdays
    preferred_time TEXT,
    -- morning, afternoon, evening

    notes TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS habit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    habit_id INTEGER REFERENCES habits(id) ON DELETE CASCADE,
    log_date DATE NOT NULL,
    completed BOOLEAN DEFAULT 1,
    count INTEGER DEFAULT 1,
    -- times completed
    duration_minutes INTEGER,
    -- actual time spent
    notes TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(habit_id, log_date)
);

CREATE TABLE IF NOT EXISTS routines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    description TEXT,
    routine_type TEXT CHECK(routine_type IN ('daily_morning', 'daily_evening', 'weekly_review', 'monthly_review', 'custom')) DEFAULT 'daily_morning',
    -- Steps as JSON array of {order, title, description, duration_minutes, type}
    steps TEXT,
    -- Tracking
    status TEXT CHECK(status IN ('active', 'paused', 'archived')) DEFAULT 'active',
    total_duration_minutes INTEGER,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS routine_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    routine_id INTEGER REFERENCES routines(id) ON DELETE CASCADE,
    log_date DATE NOT NULL,
    completed_steps TEXT,
    -- JSON array of completed step orders
    skipped_steps TEXT,
    -- JSON array of skipped step orders
    duration_minutes INTEGER,
    mood_before INTEGER CHECK(mood_before >= 1 AND mood_before <= 5),
    mood_after INTEGER CHECK(mood_after >= 1 AND mood_after <= 5),
    notes TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(routine_id, log_date)
);

CREATE INDEX IF NOT EXISTS idx_habits_status ON habits(status);

CREATE INDEX IF NOT EXISTS idx_habits_category ON habits(category);

CREATE INDEX IF NOT EXISTS idx_habit_logs_habit ON habit_logs(habit_id);

CREATE INDEX IF NOT EXISTS idx_habit_logs_date ON habit_logs(log_date);

CREATE INDEX IF NOT EXISTS idx_routines_type ON routines(routine_type);

CREATE INDEX IF NOT EXISTS idx_routine_logs_routine ON routine_logs(routine_id);

CREATE INDEX IF NOT EXISTS idx_routine_logs_date ON routine_logs(log_date);
