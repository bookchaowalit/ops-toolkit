-- The retired revenue, invoice and client tables, for the SQLite CRM ledger double only.
-- Production reads and writes them through the CRM ledger (infra/scripts/utils/crm_ledger.py).

CREATE TABLE IF NOT EXISTS clients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    company TEXT,
    email TEXT,
    phone TEXT,
    -- Status
    status TEXT CHECK(status IN ('lead', 'prospect', 'active', 'past', 'churned')) DEFAULT 'lead',
    -- Business
    niche_id INTEGER,
    contract_value REAL,
    contract_start DATE,
    contract_end DATE,
    -- Details
    source TEXT,
    -- how they found you
    pain_points TEXT,
    -- JSON array
    notes TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS revenue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    -- Source
    source_type TEXT CHECK(source_type IN ('service', 'product', 'subscription', 'affiliate', 'ad', 'sponsorship', 'other')),
    source_name TEXT,
    -- specific product/client name
    client_id INTEGER,
    asset_id INTEGER,
    -- Amount
    amount REAL NOT NULL,
    currency TEXT DEFAULT 'THB',
    -- Date
    revenue_date DATE NOT NULL,
    -- Details
    description TEXT,
    notes TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    received INTEGER DEFAULT 0,
    idempotency_key TEXT
);

CREATE TABLE IF NOT EXISTS invoices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_no TEXT NOT NULL UNIQUE,
    client_id INTEGER,
    revenue_id INTEGER,
    amount REAL NOT NULL,
    currency TEXT DEFAULT 'THB',
    issue_date DATE NOT NULL,
    due_date DATE NOT NULL,
    paid_date DATE,
    status TEXT DEFAULT 'draft' CHECK(status IN ('draft','sent','paid','overdue','cancelled')),
    description TEXT,
    notes TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
