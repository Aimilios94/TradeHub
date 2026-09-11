-- 0006 — Phase 7: the backtest entry spec, stored with the strategy.
--
-- Live, an S1/S3 entry price comes from a Hermes plan. A backtest cannot call Hermes and must not
-- pretend to — grading judgment with hindsight is not a measurement. So a backtestable strategy
-- declares a DETERMINISTIC entry rule (entry_rule, stop_rule, target_rule, bar timeframe, entry
-- window), and every `backtests` row reports which rule produced its numbers. What the backtester
-- grades is the rule plus the exit chain, never the model.
--
-- A strategy with `backtest` null is reported "not backtestable" WITH THE REASON rather than silently
-- skipped: S2 is a briefing, it has no entry, stop, target or size, and there is nothing to replay.
--
-- Validated by app/models.py::BacktestSpec. Idempotent.

alter table strategy_configs add column if not exists backtest jsonb;

comment on column strategy_configs.backtest is
  'BacktestSpec (app/models.py): the deterministic entry/stop/target rule the Phase 7 replay uses for '
  'this strategy. Null = not backtestable, and the run says why.';
