-- 0005 — Phase 4: the watchlist briefing (prompt template 03, S2).
--
-- A briefing is NOT a signal: it has no entry, stop, target or size, and nothing can ever be executed from
-- it. Keeping it out of `signals` keeps that table meaning exactly one thing — a proposed trade — so a
-- "signals today" count is never inflated by briefings (docs/DECISIONS.md #30). Idempotent.

create table if not exists briefings (
  id                 uuid primary key default gen_random_uuid(),
  briefing_date      date not null,
  kind               text not null default 'watchlist',   -- watchlist (S2) | future kinds
  strategy_id        uuid references strategy_configs(id),
  as_of_et           text,                                 -- HH:MM the inputs were computed at
  market_context     text,
  rows               jsonb not null default '[]'::jsonb,   -- template 03 rows[]: symbol, priority, bias,
                                                           -- catalyst, price_action, levels, setups
  inputs             jsonb,                                -- what the model was actually shown (auditable)
  model              text,
  prompt_template_id uuid references prompt_templates(id),
  raw_llm_response   jsonb,
  usage              jsonb,                                -- prompt/completion tokens, generation id
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now(),
  unique (briefing_date, kind)                             -- a re-run replaces the day's briefing
);
create index if not exists briefings_date_idx on briefings (briefing_date desc);
create trigger trg_briefings_updated before update on briefings for each row execute function set_updated_at();

alter table briefings enable row level security;
do $$ begin
  if not exists (select 1 from pg_policies where tablename = 'briefings' and policyname = 'owner_all') then
    create policy owner_all on briefings for all to authenticated using (true) with check (true);
  end if;
end $$;

-- signals: one plan per (strategy, symbol) per session day; a re-run replaces the day's plan.
-- signal_date is the ET session date, set by the engine (a timestamptz::date cast is not immutable).
alter table signals add column if not exists signal_date date;
update signals set signal_date = (created_at at time zone 'America/New_York')::date where signal_date is null;
create unique index if not exists signals_strategy_day_symbol_uidx on signals (strategy_id, signal_date, symbol);

-- Why a plan was refused, when it was the code re-validation rather than Hermes that refused it
-- (template 02 Notes: plans failing validation are stored rejected, never silently fixed).
alter table signals add column if not exists reject_reason text;
alter table signals add column if not exists decision text;      -- trade | watch | skip (Hermes' own call)
