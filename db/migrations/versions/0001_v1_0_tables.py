"""v1.0 tables: the 16 app-data tables from the plan.

Prices and macro history are NOT here; they live in Parquet snapshots read through DuckDB.
All timestamps are timestamptz and stored in UTC. Fixed menus are CHECK constraints, so a
value outside the menu cannot be stored.

Revision ID: 0001
Revises:
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TABLES = [
    "sources",
    "tickers",
    "driver_taxonomy",
    "driver_sensitivities",
    "watchlist",
    "llm_traces",
    "eval_runs",
    "news_items",
    "news_sentiment",
    "economic_events",
    "tagged_events",
    "market_context",
    "daily_scores",
    "strategy_configs",
    "datasets",
    "backtests",
]

UPGRADE = """
-- Source registry. One row per data source; swapping a source is a config change.
CREATE TABLE sources (
    id                 text PRIMARY KEY,
    name               text NOT NULL,
    kinds              text[] NOT NULL,
    role               text NOT NULL,
    status             text NOT NULL
        CHECK (status IN ('working', 'fallback', 'pending', 'untested', 'dropped')),
    enabled            boolean NOT NULL DEFAULT true,
    priority           smallint NOT NULL DEFAULT 1,
    credentials_ref    text,
    schedule           text,
    depth              smallint NOT NULL DEFAULT 1,
    version_introduced text NOT NULL DEFAULT 'v1.0',
    last_success_at    timestamptz,
    last_error_at      timestamptz,
    last_error         text,
    last_verified_on   date,
    notes              text
);
COMMENT ON COLUMN sources.credentials_ref IS
    'Name of the environment variable that holds the key. Never the key itself.';

-- Symbol master. Canonical ID = asset-class prefix + symbol, e.g. FUT:GC, ETF:GLD, EQ:TSLA.
CREATE TABLE tickers (
    id             text PRIMARY KEY,
    asset_class    text NOT NULL
        CHECK (asset_class IN ('EQ', 'ETF', 'FUT', 'IDX', 'SPOT', 'CRYPTO')),
    symbol         text NOT NULL,
    display_name   text NOT NULL,
    aliases        text[] NOT NULL DEFAULT '{}',
    source_symbols jsonb NOT NULL DEFAULT '{}',
    sector         text,
    is_active      boolean NOT NULL DEFAULT true,
    created_at     timestamptz NOT NULL DEFAULT now(),
    CHECK (id = asset_class || ':' || symbol),
    CHECK (symbol ~ '^[A-Z0-9.]+$')
);

-- The 7 driver tags.
CREATE TABLE driver_taxonomy (
    id                text PRIMARY KEY,
    name              text NOT NULL UNIQUE,
    meaning           text NOT NULL,
    example           text NOT NULL,
    plus_one_meaning  text NOT NULL,
    minus_one_meaning text,
    sort_order        smallint NOT NULL
);
COMMENT ON COLUMN driver_taxonomy.minus_one_meaning IS
    'NULL means the tag has no -1 direction (liquidity flush: +1 or 0 only).';

-- Sensitivity matrix: expected asset move when the driver direction is +1.
CREATE TABLE driver_sensitivities (
    id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    driver_id          text NOT NULL REFERENCES driver_taxonomy (id),
    ticker_id          text NOT NULL REFERENCES tickers (id),
    expected_direction smallint NOT NULL CHECK (expected_direction IN (-1, 0, 1)),
    horizon            text NOT NULL CHECK (horizon IN ('intraday', 'days', 'weeks')),
    status             text NOT NULL DEFAULT 'hypothesis'
        CHECK (status IN ('hypothesis', 'supported', 'rejected')),
    note               text,
    UNIQUE (driver_id, ticker_id, horizon)
);

CREATE TABLE watchlist (
    ticker_id text PRIMARY KEY REFERENCES tickers (id),
    position  smallint NOT NULL DEFAULT 0,
    is_seed   boolean NOT NULL DEFAULT false,
    added_at  timestamptz NOT NULL DEFAULT now()
);

-- Every Hermes call, valid or not.
CREATE TABLE llm_traces (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    created_at        timestamptz NOT NULL DEFAULT now(),
    template          text NOT NULL,
    prompt_version    text NOT NULL,
    tier              text NOT NULL CHECK (tier IN ('cheap', 'better')),
    model             text NOT NULL,
    attempt           smallint NOT NULL DEFAULT 1,
    latency_ms        integer,
    prompt_tokens     integer,
    completion_tokens integer,
    cost_usd          numeric(12, 6),
    outcome           text NOT NULL
        CHECK (outcome IN ('ok', 'invalid_json', 'schema_failed', 'number_check_failed',
                           'error', 'capped')),
    error             text
);
CREATE INDEX llm_traces_created_at_idx ON llm_traces (created_at);

CREATE TABLE eval_runs (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    created_at     timestamptz NOT NULL DEFAULT now(),
    eval_name      text NOT NULL,
    dataset        text NOT NULL,
    prompt_version text,
    model          text,
    code_version   text,
    is_mock        boolean NOT NULL DEFAULT false,
    scores         jsonb NOT NULL
);

-- Raw news items. ingested_at (not published_at) is the point-in-time used by backtests.
CREATE TABLE news_items (
    id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id          text NOT NULL REFERENCES sources (id),
    title              text NOT NULL,
    url                text NOT NULL,
    published_at       timestamptz,
    ingested_at        timestamptz NOT NULL DEFAULT now(),
    dedupe_hash        text NOT NULL UNIQUE,
    duplicate_of       bigint REFERENCES news_items (id),
    excerpt            text,
    raw_text           text,
    raw_text_pruned_at timestamptz,
    importance_score   numeric(8, 4),
    is_demo            boolean NOT NULL DEFAULT false
);
CREATE INDEX news_items_ingested_at_idx ON news_items (ingested_at);
COMMENT ON COLUMN news_items.raw_text IS 'Pruned after 30 days; raw_text_pruned_at records when.';
COMMENT ON COLUMN news_items.is_demo IS 'Demo rows must be labeled as demo wherever shown.';

-- Condenser output, one row per news item per ticker per prompt version.
CREATE TABLE news_sentiment (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    news_item_id        bigint NOT NULL REFERENCES news_items (id) ON DELETE CASCADE,
    ticker_id           text NOT NULL REFERENCES tickers (id),
    catalyst_type       text NOT NULL
        CHECK (catalyst_type IN ('earnings', 'guidance', 'analyst_rating', 'm_and_a', 'product',
                                 'legal_regulatory', 'management', 'macro', 'other')),
    sentiment_score     numeric(4, 3) NOT NULL CHECK (sentiment_score BETWEEN -1 AND 1),
    driver_direction    smallint NOT NULL CHECK (driver_direction IN (-1, 0, 1)),
    key_facts           jsonb NOT NULL DEFAULT '[]',
    one_sentence_reason text NOT NULL,
    prompt_version      text NOT NULL,
    model               text NOT NULL,
    llm_trace_id        bigint REFERENCES llm_traces (id),
    created_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (news_item_id, ticker_id, prompt_version)
);
CREATE INDEX news_sentiment_ticker_idx ON news_sentiment (ticker_id, created_at);

-- FOMC / CPI / NFP calendar plus per-ticker earnings dates.
CREATE TABLE economic_events (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_type        text NOT NULL
        CHECK (event_type IN ('FOMC', 'CPI', 'NFP', 'EARNINGS', 'OTHER')),
    ticker_id         text REFERENCES tickers (id),
    title             text NOT NULL,
    event_time        timestamptz NOT NULL,
    time_is_estimated boolean NOT NULL DEFAULT false,
    scheduled         boolean NOT NULL DEFAULT true,
    actual            numeric,
    forecast          numeric,
    previous          numeric,
    source_id         text REFERENCES sources (id),
    payload           jsonb NOT NULL DEFAULT '{}',
    created_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE NULLS NOT DISTINCT (event_type, ticker_id, event_time),
    CHECK ((event_type = 'EARNINGS') = (ticker_id IS NOT NULL))
);
CREATE INDEX economic_events_time_idx ON economic_events (event_time);

-- Driver tags on news items or scheduled events.
CREATE TABLE tagged_events (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    news_item_id      bigint REFERENCES news_items (id) ON DELETE CASCADE,
    economic_event_id bigint REFERENCES economic_events (id) ON DELETE CASCADE,
    driver_id         text NOT NULL REFERENCES driver_taxonomy (id),
    direction         smallint NOT NULL CHECK (direction IN (-1, 0, 1)),
    scheduled         boolean NOT NULL,
    affected_assets   text[] NOT NULL DEFAULT '{}',
    tagged_by         text NOT NULL CHECK (tagged_by IN ('code', 'hermes')),
    event_time        timestamptz NOT NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),
    CHECK (num_nonnulls(news_item_id, economic_event_id) = 1),
    CHECK (driver_id <> 'liquidity_flush' OR direction >= 0)
);
CREATE INDEX tagged_events_driver_time_idx ON tagged_events (driver_id, event_time);

-- Market snapshots: futures strip, VIX, DXY, US10Y, sector scores, Fed rates.
CREATE TABLE market_context (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    captured_at timestamptz NOT NULL DEFAULT now(),
    kind        text NOT NULL CHECK (kind IN ('strip', 'sector', 'fed', 'regime')),
    ticker_id   text REFERENCES tickers (id),
    series_id   text,
    value       numeric,
    change_pct  numeric,
    as_of       timestamptz NOT NULL,
    is_delayed  boolean NOT NULL DEFAULT true,
    source_id   text REFERENCES sources (id),
    payload     jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX market_context_kind_time_idx ON market_context (kind, captured_at);
COMMENT ON COLUMN market_context.series_id IS 'For rows that are not a ticker, e.g. a FRED series.';

-- Composite sentiment score, -100 to +100, per ticker per day.
CREATE TABLE daily_scores (
    ticker_id       text NOT NULL REFERENCES tickers (id),
    score_date      date NOT NULL,
    composite_score numeric(6, 2) NOT NULL CHECK (composite_score BETWEEN -100 AND 100),
    components      jsonb NOT NULL,
    news_count      integer NOT NULL DEFAULT 0,
    code_version    text,
    computed_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (ticker_id, score_date)
);

-- Strategies as data. A changed config is a new version, never an edit.
CREATE TABLE strategy_configs (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    slug            text NOT NULL,
    version         integer NOT NULL,
    name            text NOT NULL,
    instrument_type text NOT NULL CHECK (instrument_type IN ('ETF', 'futures')),
    config          jsonb NOT NULL,
    config_hash     text NOT NULL,
    is_active       boolean NOT NULL DEFAULT true,
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (slug, version)
);

-- Registry of frozen Parquet snapshots. A snapshot is never edited.
CREATE TABLE datasets (
    version      text PRIMARY KEY,
    path         text NOT NULL,
    content_hash text NOT NULL,
    date_start   date NOT NULL,
    date_end     date NOT NULL,
    symbols      text[] NOT NULL,
    row_counts   jsonb NOT NULL,
    manifest     jsonb NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now()
);

-- Every backtest run is kept, failures included.
CREATE TABLE backtests (
    id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    created_at         timestamptz NOT NULL DEFAULT now(),
    strategy_config_id bigint NOT NULL REFERENCES strategy_configs (id),
    config_hash        text NOT NULL,
    dataset_version    text NOT NULL REFERENCES datasets (version),
    instrument_type    text NOT NULL CHECK (instrument_type IN ('ETF', 'futures')),
    period             text NOT NULL CHECK (period IN ('tune', 'validate', 'holdout', 'custom')),
    date_start         date NOT NULL,
    date_end           date NOT NULL,
    fill_params        jsonb NOT NULL,
    code_version       text NOT NULL,
    trades_count       integer NOT NULL,
    expectancy         numeric,
    expectancy_ci_low  numeric,
    expectancy_ci_high numeric,
    profit_factor      numeric,
    win_rate           numeric,
    metrics            jsonb NOT NULL,
    confidence_label   text NOT NULL
        CHECK (confidence_label IN ('normal', 'low_confidence', 'insufficient_trades')),
    verdict            text NOT NULL CHECK (verdict IN ('pass', 'fail', 'discarded')),
    known_biases       text[] NOT NULL DEFAULT '{}'
);
COMMENT ON COLUMN backtests.win_rate IS 'Shown, never used to decide the verdict.';
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
