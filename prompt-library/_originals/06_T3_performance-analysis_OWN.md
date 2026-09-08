# T3 — Custom Trade Journal / Performance Analysis (owner's original prompt, verbatim)
Source: owner. Adapted version: plan §14 Analytics Module (reads `journal` directly; CSV importer kept as a bonus for external brokers).

script that analyzes my trading performance.

INPUT:
- Import CSV file from TD Ameritrade (or any broker)
- Required columns: Symbol, Entry Date, Entry Time, Entry Price, Exit Date, Exit Time, Exit Price, Shares, P&L
- I'll manually add a "Setup Type" column (ORB, VWAP Bounce, Momentum, etc.)

ANALYSIS TO PERFORM:
1. Overall Statistics:
   - Total trades, Winners, Losers, Win Rate
   - Average Win, Average Loss, Expectancy
   - Largest Win, Largest Loss
   - Total P&L, Average P&L per trade

2. Performance by Setup Type:
   - Win rate for each setup
   - Average win/loss for each setup
   - Total P&L by setup
   - Number of trades per setup

3. Performance by Time of Day:
   - Break trading day into hourly blocks: 9:30-10:30, 10:30-11:30, 11:30-12:30, 12:30-1:30, 1:30-2:30, 2:30-4:00
   - For each block: Win rate, Avg Win, Avg Loss, Total P&L, Number of trades

4. Performance by Day of Week:
   - Same metrics broken down Monday through Friday

5. Pattern Detection:
   - Identify best performing hour
   - Identify worst performing hour
   - Identify best performing setup
   - Identify worst performing setup
   - Flag any time periods where I'm consistently losing

OUTPUT:
- Display results as clean formatted tables
- Generate a written summary highlighting key patterns
- Provide specific recommendations based on the data

TECHNICAL REQUIREMENTS:
- Use pandas for data analysis
- Make it easy to run (simple command line: python analyze_trades.py)
- Include error handling for missing data
- Add comments explaining each section

Make this something I can run weekly on my latest trades.
