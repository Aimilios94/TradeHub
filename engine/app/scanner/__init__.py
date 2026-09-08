"""Scanner (Phase 2, plan §15). Endpoint `POST /scan/run` called by the n8n 08:00 / 11:30 ET crons.

Pull Finviz Elite CSV once → apply EVERY active strategy's `universe.params` from config →
compute detectors per survivor → rank → write `scans`. Config-driven; no hardcoded filters.
"""
