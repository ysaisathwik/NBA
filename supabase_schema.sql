-- ============================================================================
-- Intelligent NBA Platform — Supabase / Postgres schema
-- Run this in the Supabase SQL editor (or psql) before pointing the platform at
-- your project via SUPABASE_URL / SUPABASE_KEY in .env.
--
-- Embeddings are stored as JSON text and scored in Python, so pgvector is NOT
-- required for the demo. If you want native pgvector search, see the note at the
-- bottom and swap the `embedding` column type to vector(256).
-- ============================================================================

create table if not exists users (
    id text primary key,
    name text,
    email text unique,
    role text,
    tenant_id text,
    created_at timestamptz default now()
);

create table if not exists assets (
    id text primary key,
    name text,
    type text,
    location text,
    scada_id text,
    installation_date text,
    last_maintenance text,
    health_score double precision,
    serves_customers integer,
    lat double precision,
    lon double precision,
    metadata text
);
create index if not exists idx_assets_scada on assets (scada_id);

create table if not exists customers (
    id text primary key,
    name text,
    tier text,
    contract text,
    sentiment double precision,
    metadata text
);

create table if not exists tickets (
    id text primary key,
    asset_id text,
    type text,
    severity text,
    status text,
    created_at text,
    resolved_at text,
    resolution_summary text,
    metadata text
);
create index if not exists idx_tickets_asset on tickets (asset_id);

create table if not exists episodic_memory (
    id text primary key,
    event_fingerprint text,
    context_summary text,
    actions_taken text,
    outcome text,
    confidence_delta double precision,
    embedding text,
    created_at text
);
create index if not exists idx_episodic_fp on episodic_memory (event_fingerprint);

create table if not exists embeddings (
    id text primary key,
    content_type text,
    source_id text,
    chunk_text text,
    embedding text,          -- JSON array of 256 floats (Python cosine)
    metadata text
);
create index if not exists idx_embeddings_type on embeddings (content_type);

create table if not exists learning_patterns (
    pattern_hash text primary key,
    feature_vector text,
    recommended_action text,
    success_rate double precision,
    sample_count integer
);

create table if not exists audit_log (
    id text primary key,
    user_id text,
    action text,
    entity_type text,
    entity_id text,
    payload text,
    timestamp text
);

create table if not exists conversations (
    id text primary key,
    session_id text,
    user_id text,
    messages text,
    created_at text,
    resolved_at text
);

-- Optional: enable Row Level Security per tenant/role in production.
-- alter table assets enable row level security;  (define policies per your auth model)

-- ----------------------------------------------------------------------------
-- Native pgvector (optional, production-grade semantic search):
--   create extension if not exists vector;
--   alter table embeddings add column embedding_vec vector(256);
--   create index on embeddings using hnsw (embedding_vec vector_cosine_ops);
-- Then replace SupabaseStore.vector_search with an RPC over embedding_vec.
-- ----------------------------------------------------------------------------
