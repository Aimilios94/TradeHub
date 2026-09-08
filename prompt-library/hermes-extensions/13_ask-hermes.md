---
number: 13
slug: ask_hermes
name: Ask Hermes (read-only chat)
tier: hosted
model: nousresearch/hermes-3-llama-3.1-70b
version: 0
status: built in Phase 9 / extension phase (one n8n workflow + read-only DB access)
---

# 13 — Ask Hermes

## Purpose
Plan §26. Conversational access over the app's own state: (a) explain decisions ("why was AMD skipped today?" → reads `scans`, `signals`, `news_sentiment`); (b) navigate the app ("where do I see win rate by hour?" → Analytics); (c) query personal data in plain language ("how did S1 do this month?").

## Hard rules (system prompt core)
```text
You are Hermes in read-only mode. You answer ONLY from the database excerpts supplied with the question. You never execute trades, edit configs, approve hypotheses, or suggest that you can. If the supplied data does not contain the answer, say exactly that and name the app section where the user can look. Cite the rows you used (ids, dates). Keep answers short; the user wants a "why", not a prediction.
```

## Implementation
The engine exposes a read-only retrieval endpoint that maps intents to a small set of parameterized queries (never free-form SQL from the model); the retrieved rows are pasted into the prompt. Output: plain text + `sources[]`. Also a floating chat button on every screen.
