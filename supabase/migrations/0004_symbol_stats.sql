-- 0004 — Phase 2: nightly per-symbol stats for the whole tradable US-equity universe (scanner pre-filter)
-- Source: Alpaca assets + SIP daily bars (free). Replaces the Finviz Elite CSV as the universe feeder
-- (docs/DECISIONS.md #16). Idempotent.

create table if not exists symbol_stats (
  symbol             text primary key,
  as_of              date not null,                       -- last daily bar used
  exchange           text,
  name               text,
  tradable           boolean not null default true,
  marginable         boolean,
  shortable          boolean,
  easy_to_borrow     boolean,
  fractionable       boolean,
  prev_close         numeric(12,4),
  prev_open          numeric(12,4),
  prev_high          numeric(12,4),
  prev_low           numeric(12,4),
  prev_volume        bigint,
  adv20              bigint,                               -- average daily volume, 20 sessions
  adv50              bigint,
  atr14              numeric(12,4),                        -- Wilder ATR, 14 sessions
  avg_range_pct20    numeric(8,4),                         -- mean (high-low)/close, 20 sessions
  high_20d           numeric(12,4),
  low_20d            numeric(12,4),
  high_60d           numeric(12,4),
  low_60d            numeric(12,4),
  sessions           int,                                  -- daily bars available in the lookback
  shares_outstanding bigint,                               -- FMP profile: marketCap / price (approximate)
  float_shares       bigint,                               -- Finviz Elite when available; else null
  float_source       text,                                 -- finviz | fmp_shares_outstanding | null
  float_as_of        date,
  updated_at         timestamptz not null default now()
);
create index if not exists symbol_stats_prev_close_idx on symbol_stats (prev_close);
create index if not exists symbol_stats_adv20_idx on symbol_stats (adv20 desc);
create trigger trg_symbol_stats_updated before update on symbol_stats for each row execute function set_updated_at();

alter table symbol_stats enable row level security;
do $$ begin
  if not exists (select 1 from pg_policies where tablename = 'symbol_stats' and policyname = 'owner_all') then
    create policy owner_all on symbol_stats for all to authenticated using (true) with check (true);
  end if;
end $$;

-- scans: one run writes one row per (strategy, symbol); re-runs on the same session day replace, not duplicate.
-- scan_date is the ET session date, set by the engine (a timestamptz::date cast is not immutable, so no expression index).
alter table scans add column if not exists scan_date date;
update scans set scan_date = (run_at at time zone 'America/New_York')::date where scan_date is null;
create unique index if not exists scans_strategy_day_symbol_kind_uidx on scans (strategy_id, scan_date, symbol, run_kind);
