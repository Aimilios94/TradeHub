-- ============================================================================
-- TradeHub — core schema (plan §5, plus the tables the plan implies: §10, §11, §20, §29, Phase 1)
-- Single-user system. RLS on every table; policy = authenticated owner only.
-- The engine and n8n use the service-role key (bypasses RLS). The frontend uses the anon key + JWT.
-- Apply on a fresh Supabase project via the SQL editor or `supabase db push`.
-- ============================================================================

create extension if not exists "pgcrypto";

-- ---------------------------------------------------------------- enums
create type provenance_t      as enum ('pdf_canonical', 'interview_supplement', 'own', 'derived');
create type style_t           as enum ('scalp', 'day', 'swing');
create type automation_mode_t as enum ('full_auto', 'semi_auto');
create type strategy_status_t as enum ('draft', 'backtesting', 'paper', 'live', 'retired');
create type direction_t       as enum ('long', 'short');
create type signal_status_t   as enum ('proposed', 'approved', 'rejected', 'expired', 'executed');
create type order_side_t      as enum ('buy', 'sell');
create type order_source_t    as enum ('signal', 'manual', 'exit_plugin', 'position_mgmt', 'guardian');
create type hypothesis_status_t as enum ('proposed', 'backtesting', 'validated', 'rejected', 'promoted');
create type hypothesis_source_t as enum ('weekly_autopsy', 'manual', 'degradation_alert', 'order_flow_research');
create type guardian_verdict_t as enum ('green', 'yellow', 'red');
create type plugin_kind_t     as enum ('detector', 'exit');
create type prompt_tier_t     as enum ('local', 'hosted');
create type feed_t            as enum ('iex', 'sip');
create type news_source_t     as enum ('reddit', 'yahoo', 'polymarket', 'finnhub', 'firecrawl', 'other');

-- ---------------------------------------------------------------- helpers
create or replace function set_updated_at() returns trigger language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end $$;

-- ============================================================================
-- 0. Configuration tables (implied by §5/§10/§11/§20)
-- ============================================================================

create table app_settings (
  key         text primary key,
  value       jsonb not null,
  description text,
  updated_at  timestamptz not null default now()
);
create trigger trg_app_settings_updated before update on app_settings for each row execute function set_updated_at();

create table plugin_registry (
  name        text primary key,
  kind        plugin_kind_t not null,
  number      int  not null,
  description text not null,
  params      jsonb not null default '{}'::jsonb,
  status      text not null default 'planned',       -- planned | implemented
  notes       text,
  unique (kind, number)
);

create table frameworks (
  code         text primary key,                      -- 'FA' | 'FB'
  name         text not null,
  creator      text not null,
  description  text,
  rules        jsonb not null,
  library_file text not null
);

create table prompt_templates (
  id            uuid primary key default gen_random_uuid(),
  number        int  not null unique,
  slug          text not null unique,
  name          text not null,
  tier          prompt_tier_t not null,
  model         text not null,
  version       int  not null default 1,
  purpose       text,
  system_prompt text not null,
  user_template text not null,
  output_schema jsonb not null,
  library_file  text not null,
  active        boolean not null default true,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);
create trigger trg_prompt_templates_updated before update on prompt_templates for each row execute function set_updated_at();

-- ============================================================================
-- 1. strategy_configs — the central table (everything references it)
-- ============================================================================

create table strategy_configs (
  id                 uuid primary key default gen_random_uuid(),
  code               text not null unique,            -- 'S1'..'S8', 'pm_*' for Polymarket sub-strategies
  name               text not null,
  creator            text not null,
  provenance         provenance_t not null,
  style              style_t not null,
  timeframe          text not null,
  automation_mode    automation_mode_t not null,
  status             strategy_status_t not null default 'draft',
  instruments        jsonb not null default '[]'::jsonb,
  frameworks         text[] not null default '{}',
  universe           jsonb not null,                  -- {type: screener|watchlist, ...}
  entry_conditions   jsonb not null,                  -- detector expressions + parameters
  exit_logic         jsonb not null,                  -- ordered list of exit plugins + parameters
  risk               jsonb not null,                  -- sizing_rule, risk_per_trade_pct, max_concurrent, daily_loss_limit_r, session
  reentry            jsonb not null default '{"allowed": false, "max_entries": 1, "cooldown_bars": 0}'::jsonb,
  prompt_template_id uuid references prompt_templates(id),
  library_file       text not null,
  notes              text,
  status_changed_at  timestamptz not null default now(),
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now()
);
create index on strategy_configs (status);
create trigger trg_strategy_configs_updated before update on strategy_configs for each row execute function set_updated_at();

-- ============================================================================
-- 2. scans — daily screener output per strategy
-- ============================================================================

create table scans (
  id          uuid primary key default gen_random_uuid(),
  strategy_id uuid not null references strategy_configs(id),
  run_at      timestamptz not null,
  run_kind    text not null default 'morning',        -- morning | midday | manual
  symbol      text not null,
  metrics     jsonb not null,                         -- gap, relvol, float, ATR, ADV, premarket vol %, levels, feed, quality flags
  rank        int,
  passed      boolean not null default false,
  reject_reason text,
  created_at  timestamptz not null default now()
);
create index on scans (strategy_id, run_at desc);
create index on scans (symbol, run_at desc);

-- ============================================================================
-- 3. signals — Hermes/engine trade plans
-- ============================================================================

create table signals (
  id                uuid primary key default gen_random_uuid(),
  strategy_id       uuid not null references strategy_configs(id),
  scan_id           uuid references scans(id),
  symbol            text not null,
  direction         direction_t not null,
  entry_price       numeric(12,4),
  entry_type        text,                             -- buy_stop | limit | stop_limit
  stop              numeric(12,4),
  target            numeric(12,4),
  trail_pct         numeric(6,3),
  size              int,
  confidence        numeric(5,2),                     -- 0–100
  reasoning         text,
  evidence          jsonb,                            -- computed values shown on the signal card
  guardian_precheck jsonb,                            -- {verdict: green|yellow|red, checks: [...], computed_at}
  status            signal_status_t not null default 'proposed',
  expires_at        timestamptz,
  decided_at        timestamptz,
  prompt_template_id uuid references prompt_templates(id),
  raw_llm_response  jsonb,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);
create index on signals (status, created_at desc);
create index on signals (strategy_id, created_at desc);
create trigger trg_signals_updated before update on signals for each row execute function set_updated_at();

-- ============================================================================
-- 4. orders — every Alpaca order (submit / replace / cancel / fill)
-- ============================================================================

create table orders (
  id               uuid primary key default gen_random_uuid(),
  signal_id        uuid references signals(id),
  strategy_id      uuid references strategy_configs(id),
  source           order_source_t not null default 'signal',
  alpaca_order_id  text unique,
  client_order_id  text unique,
  parent_order_id  uuid references orders(id),        -- OCO legs / replacements
  symbol           text not null,
  side             order_side_t not null,
  order_type       text not null,                     -- limit | stop | stop_limit | trailing_stop | market
  time_in_force    text not null default 'day',
  qty              int not null,
  limit_price      numeric(12,4),
  stop_price       numeric(12,4),
  trail_percent    numeric(6,3),
  extended_hours   boolean not null default false,
  order_class      text,                              -- simple | oco | bracket (bracket never pre-market)
  status           text not null,                     -- Alpaca status
  status_timeline  jsonb not null default '[]'::jsonb,
  filled_qty       int not null default 0,
  filled_avg_price numeric(12,4),
  filled_at        timestamptz,
  quote_mid_at_submit numeric(12,4),                  -- for realized spread cost (Guardian Layer 2)
  submitted_at     timestamptz,
  raw_request      jsonb,
  raw_response     jsonb,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now()
);
create index on orders (symbol, created_at desc);
create index on orders (status);
create trigger trg_orders_updated before update on orders for each row execute function set_updated_at();

-- ============================================================================
-- 5. journal — one row per completed trade
-- ============================================================================

create table journal (
  id               uuid primary key default gen_random_uuid(),
  strategy_id      uuid references strategy_configs(id),
  signal_id        uuid references signals(id),
  source           order_source_t not null default 'signal',
  symbol           text not null,
  direction        direction_t not null,
  entry_time       timestamptz not null,
  entry_price      numeric(12,4) not null,
  exit_time        timestamptz,
  exit_price       numeric(12,4),
  qty              int not null,
  pnl_gross        numeric(14,4),
  fees             numeric(10,4) not null default 0,  -- SEC/TAF from Alpaca activities
  spread_cost      numeric(10,4) not null default 0,
  pnl_net          numeric(14,4),
  r_multiple       numeric(8,3),
  catalyst         text,
  setup_type       text,
  market_regime    text,
  time_block       text,                              -- premarket | 09:30-10:30 | ... (plan §14)
  planned          jsonb,                             -- planned entry/stop/target/exit rule
  actual           jsonb,                             -- actual entry/stop/exit + deviations
  subjective_note  text,                              -- user's post-trade note (feeds tilt detection)
  autopsy          jsonb,                             -- Hermes template 05 output
  autopsy_at       timestamptz,
  trade_number_of_day int,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now()
);
create index on journal (strategy_id, entry_time desc);
create index on journal (symbol, entry_time desc);
create trigger trg_journal_updated before update on journal for each row execute function set_updated_at();

-- ============================================================================
-- 6. news_sentiment — per symbol per day per source (local 8B condenser output)
-- ============================================================================

create table news_sentiment (
  id            uuid primary key default gen_random_uuid(),
  symbol        text not null,
  trade_date    date not null,
  source        news_source_t not null,
  catalyst_type text,
  sentiment_score numeric(4,3),                       -- -1..1
  key_facts     jsonb not null default '[]'::jsonb,
  one_sentence_reason text,
  raw_items     jsonb,                                -- the headlines/threads condensed (small)
  model         text,
  created_at    timestamptz not null default now(),
  unique (symbol, trade_date, source)
);
create index on news_sentiment (symbol, trade_date desc);

-- ============================================================================
-- 7. backtests
-- ============================================================================

create table backtests (
  id              uuid primary key default gen_random_uuid(),
  strategy_id     uuid not null references strategy_configs(id),
  hypothesis_id   uuid,                               -- fk added after hypotheses is created
  config_snapshot jsonb not null,
  date_from       date not null,
  date_to         date not null,
  feed            feed_t not null default 'sip',
  fill_model      jsonb not null,                     -- spread, partial fills, slippage ticks, queue rule
  metrics         jsonb,                              -- trades, win_rate, expectancy, avg_win, avg_loss, profit_factor, max_dd, per_year, per_regime
  coverage_stats  jsonb,                              -- survivorship: symbols requested/found/delisted
  calibration     jsonb,                              -- IEX↔SIP factor used
  verdict         text,                               -- pass | fail | inconclusive (vs promotion_criteria)
  verdict_detail  jsonb,
  ab_against_backtest_id uuid references backtests(id),
  status          text not null default 'queued',     -- queued | running | done | error
  error           text,
  started_at      timestamptz,
  finished_at     timestamptz,
  created_at      timestamptz not null default now()
);
create index on backtests (strategy_id, created_at desc);

-- ============================================================================
-- 8. guardian_ledger — fees, spread cost, realized P&L, tax provision, FX
-- ============================================================================

create table guardian_ledger (
  id             uuid primary key default gen_random_uuid(),
  event_time     timestamptz not null,
  kind           text not null,                       -- fee_sec | fee_taf | spread_cost | realized_pnl | tax_provision | fx | funding
  symbol         text,
  order_id       uuid references orders(id),
  journal_id     uuid references journal(id),
  amount         numeric(14,4) not null,
  currency       text not null default 'USD',
  running_realized_pnl numeric(14,4),
  running_tax_provision numeric(14,4),                -- 28% of net realized gains, display only
  alpaca_activity_id text unique,
  detail         jsonb,
  created_at     timestamptz not null default now()
);
create index on guardian_ledger (event_time desc);

-- ============================================================================
-- 9. compliance_events — Guardian warnings/blocks, margin calls, rules updates, kill switch, failures
-- ============================================================================

create table compliance_events (
  id          uuid primary key default gen_random_uuid(),
  event_time  timestamptz not null default now(),
  kind        text not null,                          -- gate_block | gate_warn | margin_call | rules_update | kill_switch | failure | blackout
  verdict     guardian_verdict_t,
  symbol      text,
  signal_id   uuid references signals(id),
  order_id    uuid references orders(id),
  reason      text not null,
  detail      jsonb,
  acknowledged boolean not null default false
);
create index on compliance_events (event_time desc);

-- ============================================================================
-- 10. hypotheses — every improvement suggestion (plan §20)
-- ============================================================================

create table hypotheses (
  id              uuid primary key default gen_random_uuid(),
  strategy_id     uuid not null references strategy_configs(id),
  title           text not null,
  proposed_change jsonb not null,                     -- config diff
  why             text not null,                      -- Hermes reasoning + cited journal evidence
  evidence        jsonb,                              -- journal ids / stats cited
  source          hypothesis_source_t not null,
  backtest_id     uuid references backtests(id),
  status          hypothesis_status_t not null default 'proposed',
  decision_note   text,
  decided_at      timestamptz,
  promoted_at     timestamptz,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);
create index on hypotheses (strategy_id, status);
create trigger trg_hypotheses_updated before update on hypotheses for each row execute function set_updated_at();
alter table backtests add constraint backtests_hypothesis_fk foreign key (hypothesis_id) references hypotheses(id);

-- ============================================================================
-- 11. economic_events (plan §29) + FRED/EIA values (plan §35)
-- ============================================================================

create table economic_events (
  id          uuid primary key default gen_random_uuid(),
  event_time  timestamptz not null,
  kind        text not null,                          -- FOMC | CPI | NFP | GDP | PCE | EIA_crude | earnings | other
  title       text not null,
  impact      text not null default 'high',           -- high | medium | low
  actual      numeric,
  expected    numeric,
  previous    numeric,
  source      text,                                   -- fred | eia | finnhub | manual
  detail      jsonb,
  created_at  timestamptz not null default now(),
  unique (event_time, kind, title)
);
create index on economic_events (event_time);

-- ============================================================================
-- 12. Market data store (plan §5 bar policy + Phase 1 baseline + calibration)
-- ============================================================================

create table bars_1min (
  symbol text not null,
  ts     timestamptz not null,
  feed   feed_t not null default 'iex',
  open   numeric(12,4) not null,
  high   numeric(12,4) not null,
  low    numeric(12,4) not null,
  close  numeric(12,4) not null,
  volume bigint not null,
  vwap   numeric(12,4),
  trade_count int,
  primary key (symbol, ts, feed)
);
-- rolling ~90 days only; deep history lives in parquet on the home server (plan §5)

create table bars_daily (
  symbol     text not null,
  trade_date date not null,
  feed       feed_t not null default 'sip',
  open   numeric(12,4) not null,
  high   numeric(12,4) not null,
  low    numeric(12,4) not null,
  close  numeric(12,4) not null,
  volume bigint not null,
  primary key (symbol, trade_date, feed)
);

create table premarket_volume_baseline (
  symbol           text not null,
  trade_date       date not null,
  feed             feed_t not null default 'iex',
  premarket_volume bigint not null,                   -- sum of 1-min bars 04:00–09:30 ET that day
  print_count      int,
  primary key (symbol, trade_date, feed)
);
-- the 14-day average is computed over the previous 14 rows for (symbol, feed) — same feed, apples to apples

create table feed_calibration (
  symbol      text not null,
  trade_date  date not null,
  session_window text not null default 'premarket',   -- premarket | regular
  iex_volume  bigint not null,
  sip_volume  bigint not null,
  ratio       numeric(10,6) generated always as (case when sip_volume > 0 then iex_volume::numeric / sip_volume else null end) stored,
  primary key (symbol, trade_date, session_window)
);

-- ============================================================================
-- 13. RLS — single authenticated owner; service role bypasses
-- ============================================================================

do $$
declare t text;
begin
  for t in
    select tablename from pg_tables where schemaname = 'public'
  loop
    execute format('alter table public.%I enable row level security', t);
    execute format('create policy owner_all on public.%I for all to authenticated using (true) with check (true)', t);
  end loop;
end $$;

-- ============================================================================
-- 14. Convenience views
-- ============================================================================

create view v_active_strategies as
  select * from strategy_configs where status in ('paper', 'live');

create view v_open_signals as
  select s.*, c.code as strategy_code, c.name as strategy_name, c.automation_mode
  from signals s join strategy_configs c on c.id = s.strategy_id
  where s.status = 'proposed' and (s.expires_at is null or s.expires_at > now());

create view v_strategy_track_record as
  select c.id as strategy_id, c.code, c.name, c.status,
         count(j.id)                                   as trades,
         sum(case when j.pnl_net > 0 then 1 else 0 end) as winners,
         round(avg(j.pnl_net), 2)                        as avg_pnl_net,
         round(sum(j.pnl_net), 2)                        as total_pnl_net,
         round(avg(j.r_multiple), 3)                     as expectancy_r
  from strategy_configs c left join journal j on j.strategy_id = c.id and j.exit_time is not null
  group by c.id, c.code, c.name, c.status;
