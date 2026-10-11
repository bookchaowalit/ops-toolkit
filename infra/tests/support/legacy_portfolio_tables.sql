-- The retired portfolio tables (goals, projects, roles-skills cells), for the SQLite test doubles only.
-- Production reads and writes these through the cell APIs (infra/scripts/utils/cell_store.py).

CREATE TABLE IF NOT EXISTS goals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT,
    -- Type & timeframe
    type TEXT CHECK(type IN ('yearly', 'quarterly', 'monthly', 'weekly', 'project')),
    category TEXT CHECK(category IN ('tech', 'business', 'life', 'creative', 'financial', 'health')),
    -- Status
    status TEXT CHECK(status IN ('planning', 'active', 'completed', 'abandoned', 'paused')) DEFAULT 'planning',
    priority INTEGER DEFAULT 3,
    -- Timeline
    start_date DATE,
    due_date DATE,
    completed_at DATETIME,
    -- Progress
    progress_percent INTEGER DEFAULT 0,
    -- Metrics
    key_results TEXT,
    -- JSON array of measurable outcomes

    notes TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    domain TEXT
);

CREATE TABLE IF NOT EXISTS goal_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_id INTEGER REFERENCES goals(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    description TEXT,
    status TEXT CHECK(status IN ('todo', 'in_progress', 'done', 'blocked')) DEFAULT 'todo',
    priority INTEGER DEFAULT 3,
    due_date DATE,
    completed_at DATETIME,
    sort_order INTEGER DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    progress_percent INTEGER NOT NULL DEFAULT 0 CHECK(progress_percent >= 0 AND progress_percent <= 100)
);

CREATE TABLE IF NOT EXISTS goal_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_id INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
    metric_key TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    metric_type TEXT NOT NULL DEFAULT 'key_result'
        CHECK(metric_type IN ('key_result', 'kpi', 'guardrail')),
    direction TEXT NOT NULL DEFAULT 'increase'
        CHECK(direction IN ('increase', 'decrease', 'maintain')),
    unit TEXT NOT NULL DEFAULT 'count',
    baseline_value REAL,
    target_value REAL NOT NULL,
    current_value REAL,
    status TEXT NOT NULL DEFAULT 'no_data'
        CHECK(status IN ('no_data', 'on_track', 'at_risk', 'off_track', 'achieved')),
    source_type TEXT NOT NULL DEFAULT 'manual'
        CHECK(source_type IN ('manual', 'task', 'ai_telemetry', 'system')),
    source_ref TEXT,
    evidence_ref TEXT,
    period_start DATE,
    period_end DATE,
    notes TEXT,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(goal_id, metric_key),
    CHECK(period_end IS NULL OR period_start IS NULL OR period_end >= period_start)
);

CREATE TABLE IF NOT EXISTS goal_portfolio_mappings (
    goal_id INTEGER PRIMARY KEY REFERENCES goals(id) ON DELETE CASCADE,
    portfolio_bucket TEXT NOT NULL CHECK (portfolio_bucket IN (
        'active_bet',
        'infrastructure',
        'maintenance',
        'capability_backlog',
        'option_portfolio',
        'large_scale_builder',
        'frontier',
        'someday_curiosity'
    )),
    attention_class TEXT NOT NULL CHECK (attention_class IN (
        'focus',
        'parallel',
        'background',
        'maintenance',
        'explore',
        'option'
    )),
    attention_cost INTEGER NOT NULL DEFAULT 0 CHECK (attention_cost >= 0 AND attention_cost <= 5),
    collision_group TEXT NOT NULL,
    canonical_goal TEXT NOT NULL,
    canonical_goal_id INTEGER REFERENCES goals(id) ON DELETE SET NULL,
    current_season TEXT NOT NULL,
    mapping_confidence TEXT NOT NULL DEFAULT 'provisional' CHECK (mapping_confidence IN (
        'high',
        'provisional',
        'review'
    )),
    mapping_source TEXT NOT NULL DEFAULT 'goal-portfolio-rule-v1',
    notes TEXT,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS productivity_goals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    goal_type TEXT NOT NULL CHECK(goal_type IN (
        'daily_completions', /* Complete N tasks per day */
        'weekly_completions', /* Complete N tasks per week */
        'daily_time', /* Track N hours per day */
        'weekly_time', /* Track N hours per week */
        'streak' /* Maintain N day completion streak */
    )),
    target_value REAL NOT NULL,
    current_value REAL DEFAULT 0,
    -- Period
    period_start DATE,
    period_end DATE,
    -- State
    is_active BOOLEAN DEFAULT 1,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS goal_project_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_id INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL DEFAULT 'delivery' CHECK(relation_type IN ('delivery', 'supporting')),
    weight REAL NOT NULL DEFAULT 1 CHECK(weight > 0),
    source TEXT NOT NULL DEFAULT 'manual',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(goal_id, project_id)
);

CREATE TABLE IF NOT EXISTS goal_context_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_id INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
    context_id INTEGER NOT NULL REFERENCES portfolio_contexts(id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL DEFAULT 'primary' CHECK(relation_type IN ('primary', 'supporting')),
    weight REAL NOT NULL DEFAULT 1 CHECK(weight > 0),
    source TEXT NOT NULL DEFAULT 'manual',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(goal_id, context_id)
);

CREATE TABLE IF NOT EXISTS goal_task_project_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_task_id INTEGER NOT NULL REFERENCES goal_tasks(id) ON DELETE CASCADE,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL DEFAULT 'delivery' CHECK(relation_type IN ('delivery', 'supporting')),
    weight REAL NOT NULL DEFAULT 1 CHECK(weight > 0),
    source TEXT NOT NULL DEFAULT 'manual',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(goal_task_id, project_id)
);

CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    description TEXT,
    -- Classification
    category TEXT CHECK(category IN ('product', 'client', 'internal', 'experiment', 'portfolio', 'open_source')) DEFAULT 'product',
    type TEXT,
    -- crm, saas, website, tool, library, etc.

    -- Status
    status TEXT CHECK(status IN ('idea', 'planning', 'active', 'paused', 'completed', 'abandoned', 'archived')) DEFAULT 'idea',
    priority INTEGER DEFAULT 3,
    -- 1=highest, 5=lowest

    -- Timeline
    start_date DATE,
    target_launch DATE,
    launched_at DATE,
    completed_at DATE,
    -- Progress
    progress_percent INTEGER DEFAULT 0 CHECK(progress_percent >= 0 AND progress_percent <= 100),
    phase TEXT,
    -- current phase name

    -- Business
    revenue_model TEXT,
    monthly_revenue REAL DEFAULT 0,
    total_revenue REAL DEFAULT 0,
    tech_stack TEXT,
    -- JSON array of technologies

    -- Links
    repo_url TEXT,
    live_url TEXT,
    docs_url TEXT,
    notes TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    color TEXT,
    parent_project_id INTEGER REFERENCES projects(id),
    task_count INTEGER DEFAULT 0,
    completed_count INTEGER DEFAULT 0,
    archived_at DATETIME,
    domain TEXT
);

CREATE TABLE IF NOT EXISTS project_repos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
    repo_name TEXT NOT NULL,
    -- GitHub repo name
    repo_url TEXT,
    role TEXT CHECK(role IN ('frontend', 'backend', 'fullstack', 'docs', 'infra', 'script', 'mobile', 'other')) DEFAULT 'fullstack',
    language TEXT,
    is_primary BOOLEAN DEFAULT 0,
    -- main repo for the project
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(project_id, repo_name)
);

CREATE TABLE IF NOT EXISTS portfolio_contexts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    context_type TEXT NOT NULL CHECK(context_type IN (
        'personal', 'hobby', 'learning', 'freelance', 'startup', 'venture',
        'client_engagement', 'internal', 'business_system', 'workspace',
        'role_capability', 'unclassified'
    )),
    scope TEXT NOT NULL DEFAULT 'personal' CHECK(scope IN ('personal', 'professional', 'client', 'mixed')),
    parent_context_id INTEGER REFERENCES portfolio_contexts(id) ON DELETE SET NULL,
    description TEXT,
    source_path TEXT,
    source TEXT NOT NULL DEFAULT 'manual',
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('planned', 'active', 'paused', 'completed', 'archived')),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS project_context_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    context_id INTEGER NOT NULL REFERENCES portfolio_contexts(id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL DEFAULT 'primary' CHECK(relation_type IN ('primary', 'supporting')),
    weight REAL NOT NULL DEFAULT 1 CHECK(weight > 0),
    source TEXT NOT NULL DEFAULT 'manual',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(project_id, context_id)
);

CREATE TABLE IF NOT EXISTS portfolio_work_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_type TEXT NOT NULL CHECK(item_type IN ('epic', 'story')),
    title TEXT NOT NULL,
    description TEXT,
    goal_id INTEGER REFERENCES goals(id) ON DELETE CASCADE,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    parent_item_id INTEGER REFERENCES portfolio_work_items(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'todo' CHECK(status IN ('todo', 'in_progress', 'done', 'blocked', 'cancelled')),
    progress_percent INTEGER NOT NULL DEFAULT 0 CHECK(progress_percent >= 0 AND progress_percent <= 100),
    weight REAL NOT NULL DEFAULT 1 CHECK(weight > 0),
    source_key TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL DEFAULT 'manual',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS work_item_task_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    work_item_id INTEGER NOT NULL REFERENCES portfolio_work_items(id) ON DELETE CASCADE,
    task_id INTEGER NOT NULL,
    relation_type TEXT NOT NULL DEFAULT 'implements' CHECK(relation_type IN ('implements', 'supports')),
    weight REAL NOT NULL DEFAULT 1 CHECK(weight > 0),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(work_item_id, task_id)
);

CREATE TABLE IF NOT EXISTS work_item_goal_task_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    work_item_id INTEGER NOT NULL REFERENCES portfolio_work_items(id) ON DELETE CASCADE,
    goal_task_id INTEGER NOT NULL REFERENCES goal_tasks(id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL DEFAULT 'implements' CHECK(relation_type IN ('implements', 'supports')),
    weight REAL NOT NULL DEFAULT 1 CHECK(weight > 0),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(work_item_id, goal_task_id)
);

CREATE TABLE IF NOT EXISTS personal_roles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    description TEXT,
    source_path TEXT,
    source TEXT NOT NULL DEFAULT 'manual',
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('planned', 'active', 'paused', 'retired')),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS personal_role_context_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    personal_role_id INTEGER NOT NULL REFERENCES personal_roles(id) ON DELETE CASCADE,
    context_id INTEGER NOT NULL REFERENCES portfolio_contexts(id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL DEFAULT 'belongs_to' CHECK(relation_type IN ('belongs_to', 'supports')),
    source TEXT NOT NULL DEFAULT 'manual',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(personal_role_id, context_id)
);

CREATE TABLE IF NOT EXISTS personal_role_goal_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    personal_role_id INTEGER NOT NULL REFERENCES personal_roles(id) ON DELETE CASCADE,
    goal_id INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
    link_type TEXT NOT NULL DEFAULT 'primary' CHECK(link_type IN ('primary', 'supporting')),
    source TEXT NOT NULL DEFAULT 'manual',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(personal_role_id, goal_id)
);

CREATE TABLE IF NOT EXISTS personal_role_project_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    personal_role_id INTEGER NOT NULL REFERENCES personal_roles(id) ON DELETE CASCADE,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    link_type TEXT NOT NULL DEFAULT 'primary' CHECK(link_type IN ('primary', 'supporting')),
    source TEXT NOT NULL DEFAULT 'manual',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(personal_role_id, project_id)
);

CREATE TABLE IF NOT EXISTS personal_role_task_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    personal_role_id INTEGER NOT NULL REFERENCES personal_roles(id) ON DELETE CASCADE,
    task_id INTEGER NOT NULL,
    link_type TEXT NOT NULL DEFAULT 'primary' CHECK(link_type IN ('primary', 'supporting')),
    source TEXT NOT NULL DEFAULT 'manual',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(personal_role_id, task_id)
);

CREATE TABLE IF NOT EXISTS role_goal_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role_id TEXT NOT NULL,
    goal_id INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
    link_type TEXT NOT NULL DEFAULT 'primary' CHECK(link_type IN ('primary', 'supporting')),
    source TEXT NOT NULL DEFAULT 'manual',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    priority INTEGER NOT NULL DEFAULT 3,
    allocation_percent INTEGER NOT NULL DEFAULT 0,
    UNIQUE(role_id, goal_id)
);

CREATE TABLE IF NOT EXISTS skill_categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    slug TEXT NOT NULL UNIQUE,
    description TEXT,
    icon TEXT,
    color TEXT,
    parent_id INTEGER REFERENCES skill_categories(id),
    sort_order INTEGER DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS skills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    description TEXT,
    category_id INTEGER REFERENCES skill_categories(id),
    -- ระดับความรู้
    level TEXT CHECK(level IN ('L1_deep', 'L2_capable', 'L3_overview', 'L4_outsource')),
    target_level TEXT CHECK(target_level IN ('L1_deep', 'L2_capable', 'L3_overview', 'L4_outsource')),
    -- สถานะ
    status TEXT CHECK(status IN ('not_started', 'learning', 'practicing', 'proficient', 'expert')) DEFAULT 'not_started',
    priority INTEGER DEFAULT 3,
    -- 1=highest, 5=lowest

    -- Progress tracking
    progress_percent INTEGER DEFAULT 0 CHECK(progress_percent >= 0 AND progress_percent <= 100),
    hours_invested REAL DEFAULT 0,
    started_at DATE,
    target_completion_date DATE,
    -- Notes
    key_concepts TEXT,
    -- JSON array
    resources TEXT,
    -- JSON array of {title, url, type}
    notes TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    revenue_relevant INTEGER DEFAULT 0,
    last_practiced DATE
);

CREATE TABLE IF NOT EXISTS role_path_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role_id TEXT NOT NULL,
    step_key TEXT NOT NULL,
    phase_id TEXT NOT NULL CHECK(phase_id IN ('orient', 'learn', 'practice', 'evidence', 'operate')),
    title TEXT NOT NULL,
    action TEXT NOT NULL,
    completion_rule TEXT NOT NULL,
    skill_id INTEGER REFERENCES skills(id) ON DELETE SET NULL,
    target_progress INTEGER CHECK(target_progress >= 0 AND target_progress <= 100),
    evidence_path TEXT,
    source_registry TEXT NOT NULL DEFAULT 'docs/ROLE-PATHS.yaml',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(role_id, step_key, source_registry)
);

CREATE TABLE IF NOT EXISTS role_project_allocations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role_id TEXT NOT NULL,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    goal_id INTEGER REFERENCES goals(id) ON DELETE CASCADE,
    allocation_percent INTEGER NOT NULL DEFAULT 0 CHECK(allocation_percent >= 0 AND allocation_percent <= 100),
    priority INTEGER NOT NULL DEFAULT 3 CHECK(priority >= 1 AND priority <= 5),
    status TEXT NOT NULL DEFAULT 'planned' CHECK(status IN ('planned', 'active', 'paused', 'completed')),
    source TEXT NOT NULL DEFAULT 'manual',
    start_date DATE,
    end_date DATE,
    notes TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(role_id, project_id, goal_id)
);

CREATE TABLE IF NOT EXISTS role_skill_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role_id TEXT NOT NULL,
    skill_id INTEGER NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
    learning_path TEXT NOT NULL,
    link_type TEXT NOT NULL DEFAULT 'core' CHECK(link_type IN ('core', 'supporting')),
    source_registry TEXT NOT NULL DEFAULT 'docs/ROLE-LEARNING.yaml',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(role_id, skill_id)
);

CREATE INDEX IF NOT EXISTS idx_skills_category ON skills(category_id);
CREATE INDEX IF NOT EXISTS idx_skills_status ON skills(status);
CREATE INDEX IF NOT EXISTS idx_skills_level ON skills(level);
CREATE INDEX IF NOT EXISTS idx_goal_tasks_goal ON goal_tasks(goal_id);
CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status);
CREATE INDEX IF NOT EXISTS idx_projects_category ON projects(category);
CREATE INDEX IF NOT EXISTS idx_projects_priority ON projects(priority);
CREATE INDEX IF NOT EXISTS idx_project_repos_project ON project_repos(project_id);
CREATE INDEX IF NOT EXISTS idx_project_repos_repo ON project_repos(repo_name);
CREATE INDEX IF NOT EXISTS idx_skills_revenue ON skills(revenue_relevant);
CREATE INDEX IF NOT EXISTS idx_projects_parent ON projects(parent_project_id);
CREATE INDEX IF NOT EXISTS idx_goals_active ON productivity_goals(is_active);
CREATE INDEX IF NOT EXISTS idx_goals_type ON productivity_goals(goal_type);
CREATE INDEX IF NOT EXISTS idx_role_skill_links_role ON role_skill_links(role_id);
CREATE INDEX IF NOT EXISTS idx_role_skill_links_skill ON role_skill_links(skill_id);
CREATE INDEX IF NOT EXISTS idx_role_path_steps_role ON role_path_steps(role_id);
CREATE INDEX IF NOT EXISTS idx_role_path_steps_phase ON role_path_steps(phase_id);
CREATE INDEX IF NOT EXISTS idx_role_path_steps_skill ON role_path_steps(skill_id);
CREATE INDEX IF NOT EXISTS idx_role_goal_links_role ON role_goal_links(role_id);
CREATE INDEX IF NOT EXISTS idx_role_goal_links_goal ON role_goal_links(goal_id);
CREATE INDEX IF NOT EXISTS idx_role_project_allocations_role ON role_project_allocations(role_id);
CREATE INDEX IF NOT EXISTS idx_role_project_allocations_project ON role_project_allocations(project_id);
CREATE INDEX IF NOT EXISTS idx_role_project_allocations_goal ON role_project_allocations(goal_id);
CREATE INDEX IF NOT EXISTS idx_goal_project_links_goal ON goal_project_links(goal_id);
CREATE INDEX IF NOT EXISTS idx_goal_project_links_project ON goal_project_links(project_id);
CREATE INDEX IF NOT EXISTS idx_portfolio_work_items_goal ON portfolio_work_items(goal_id);
CREATE INDEX IF NOT EXISTS idx_portfolio_work_items_project ON portfolio_work_items(project_id);
CREATE INDEX IF NOT EXISTS idx_portfolio_work_items_parent ON portfolio_work_items(parent_item_id);
CREATE INDEX IF NOT EXISTS idx_portfolio_contexts_parent ON portfolio_contexts(parent_context_id);
CREATE INDEX IF NOT EXISTS idx_portfolio_contexts_type ON portfolio_contexts(context_type, scope);
CREATE INDEX IF NOT EXISTS idx_personal_roles_category ON personal_roles(category);
CREATE INDEX IF NOT EXISTS idx_goal_context_links_goal ON goal_context_links(goal_id);
CREATE INDEX IF NOT EXISTS idx_goal_context_links_context ON goal_context_links(context_id);
CREATE INDEX IF NOT EXISTS idx_project_context_links_project ON project_context_links(project_id);
CREATE INDEX IF NOT EXISTS idx_project_context_links_context ON project_context_links(context_id);
CREATE INDEX IF NOT EXISTS idx_personal_role_context_links_role ON personal_role_context_links(personal_role_id);
CREATE INDEX IF NOT EXISTS idx_personal_role_context_links_context ON personal_role_context_links(context_id);
CREATE INDEX IF NOT EXISTS idx_personal_role_goal_links_role ON personal_role_goal_links(personal_role_id);
CREATE INDEX IF NOT EXISTS idx_personal_role_goal_links_goal ON personal_role_goal_links(goal_id);
CREATE INDEX IF NOT EXISTS idx_personal_role_project_links_role ON personal_role_project_links(personal_role_id);
CREATE INDEX IF NOT EXISTS idx_personal_role_project_links_project ON personal_role_project_links(project_id);
CREATE INDEX IF NOT EXISTS idx_goal_task_project_links_goal_task ON goal_task_project_links(goal_task_id);
CREATE INDEX IF NOT EXISTS idx_goal_task_project_links_project ON goal_task_project_links(project_id);
CREATE INDEX IF NOT EXISTS idx_work_item_goal_task_links_item ON work_item_goal_task_links(work_item_id);
CREATE INDEX IF NOT EXISTS idx_work_item_goal_task_links_goal_task ON work_item_goal_task_links(goal_task_id);
CREATE INDEX IF NOT EXISTS idx_goal_metrics_goal_status
    ON goal_metrics(goal_id, status, metric_type);
CREATE INDEX IF NOT EXISTS idx_goal_portfolio_bucket
    ON goal_portfolio_mappings(portfolio_bucket, current_season);
CREATE INDEX IF NOT EXISTS idx_goal_portfolio_attention
    ON goal_portfolio_mappings(attention_class, attention_cost, current_season);
CREATE INDEX IF NOT EXISTS idx_goal_portfolio_collision
    ON goal_portfolio_mappings(collision_group, current_season);
CREATE INDEX IF NOT EXISTS idx_goal_portfolio_canonical
    ON goal_portfolio_mappings(canonical_goal, current_season);
CREATE INDEX IF NOT EXISTS idx_personal_role_task_links_role ON personal_role_task_links(personal_role_id);
CREATE INDEX IF NOT EXISTS idx_personal_role_task_links_task ON personal_role_task_links(task_id);
CREATE INDEX IF NOT EXISTS idx_work_item_task_links_item ON work_item_task_links(work_item_id);
CREATE INDEX IF NOT EXISTS idx_work_item_task_links_task ON work_item_task_links(task_id);

CREATE VIEW IF NOT EXISTS goal_portfolio_current AS
SELECT
    g.id AS goal_id,
    g.title,
    g.description,
    g.type,
    g.category,
    g.status,
    g.priority,
    g.progress_percent,
    g.due_date,
    m.portfolio_bucket,
    m.attention_class,
    m.attention_cost,
    m.collision_group,
    m.canonical_goal,
    m.canonical_goal_id,
    m.current_season,
    m.mapping_confidence,
    m.mapping_source,
    m.notes AS mapping_notes,
    m.updated_at AS mapping_updated_at
FROM goals g
JOIN goal_portfolio_mappings m ON m.goal_id = g.id;

CREATE VIEW IF NOT EXISTS goal_attention_collision_summary AS
WITH canonical_families AS (
    SELECT
        m.current_season,
        m.attention_class,
        m.attention_cost,
        m.collision_group,
        m.canonical_goal
    FROM goal_portfolio_mappings m
    JOIN goals g ON g.id = m.goal_id
    WHERE g.status IN ('active', 'planning')
    GROUP BY
        m.current_season,
        m.attention_class,
        m.attention_cost,
        m.collision_group,
        m.canonical_goal
)
SELECT
    current_season,
    attention_class,
    collision_group,
    COUNT(*) AS canonical_goal_count,
    SUM(attention_cost) AS attention_cost
FROM canonical_families
GROUP BY current_season, attention_class, collision_group;


CREATE TABLE IF NOT EXISTS progress_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_id INTEGER,
    -- What was done
    activity_type TEXT CHECK(activity_type IN ('study', 'practice', 'project', 'teaching', 'review', 'other')),
    title TEXT NOT NULL,
    description TEXT,
    -- Metrics
    hours_spent REAL,
    progress_before INTEGER,
    progress_after INTEGER,
    -- Reflection
    what_learned TEXT,
    challenges TEXT,
    next_steps TEXT,
    -- Links
    evidence_url TEXT,
    -- link to proof (commit, article, etc.)

    logged_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_progress_log_skill ON progress_log(skill_id);
CREATE INDEX IF NOT EXISTS idx_progress_log_date ON progress_log(logged_at);
