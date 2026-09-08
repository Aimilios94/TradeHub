# T1 — Custom Order Entry / Exit Logic: 2-bar trailing stop (owner's original prompt, verbatim)
Source: owner. NOTE: written in Pine Script terms; our implementation is an ENGINE EXIT PLUGIN in Python (plan §9, exit plugin #3). Visual requirements belong to backtest report charts / UI phase.

strategy that implements a 2-bar trailing stop.

ENTRY:
- Strategy will use strategy.entry() for long positions
- User can manually define entry conditions or I'll enter on simple MA cross for demo

STOP LOSS LOGIC:
- Initial stop: Lowest low of the past 2 bars at time of entry
- Trailing mechanism:
  * Every time price makes a new 2-bar high (current high > high of past 2 bars)
  * Move stop to the lowest low of the past 2 bars
  * Stop only moves UP, never down
- Exit when stop is hit using strategy.exit()

VISUAL REQUIREMENTS:
- Plot the stop line on the chart
- Color coding: Red when initially set, Green when trailing (has moved up at least once)
- Show entry points with a triangle marker
- Show exit points with a square marker

SETTINGS:
- Make the lookback period (2 bars) adjustable via input
- Add option to show/hide the stop line

Provide clean, well-commented code suitable for backtesting.
