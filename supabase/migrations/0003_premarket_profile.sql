-- 0003 — Phase 1: cumulative pre-market volume profile + calibration helper view
-- Idempotent. Applies on top of 0001/0002.

-- cumulative volume at fixed ET checkpoints ({"05:00": n, ..., "09:30": n}); lets the 08:00 scan compare
-- today's volume-by-08:00 with the 14-day average volume-by-08:00 on the same feed (plan §3.2 item 2, §6 S1).
alter table premarket_volume_baseline add column if not exists cum_profile jsonb;

create index if not exists premarket_volume_baseline_feed_date_idx on premarket_volume_baseline (feed, trade_date desc);
create index if not exists feed_calibration_window_date_idx on feed_calibration (session_window, trade_date desc);

-- 14-day same-feed average of the full pre-market window per symbol (full-window view; checkpoint math lives in the engine)
create or replace view v_premarket_baseline_14d as
select symbol, feed,
       avg(premarket_volume)::bigint as avg_premarket_volume_14d,
       count(*)                      as days_used,
       max(trade_date)               as latest_date
from (
  select symbol, feed, trade_date, premarket_volume,
         row_number() over (partition by symbol, feed order by trade_date desc) as rn
  from premarket_volume_baseline
) t
where rn <= 14
group by symbol, feed;

-- median IEX/SIP ratio per window over the trailing 30 days (plan §13 trap 2 — the calibration factor)
create or replace view v_feed_calibration_30d as
select session_window,
       count(*)                                                  as samples,
       percentile_cont(0.5) within group (order by ratio)        as median_iex_over_sip,
       avg(case when iex_volume = 0 and sip_volume > 0 then 1 else 0 end)::numeric(6,4) as zero_iex_share
from feed_calibration
where trade_date >= current_date - 30 and ratio is not null
group by session_window;
