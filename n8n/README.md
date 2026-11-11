# n8n workflows

n8n only triggers jobs on a schedule. It holds no logic: each workflow is a Schedule Trigger followed by one HTTP Request to the API.

- UI: http://127.0.0.1:5680 (create the owner account on first open).
- Inside the Compose network the API is `http://api:8000`.

## Calling a job

```
POST http://api:8000/jobs/<job_name>
Header: X-Trigger-Secret: {{ $env.N8N_TRIGGER_SECRET }}
```

The secret comes from `.env` and is passed into the n8n container by Compose.

## Jobs

| Job | What it does | Since |
|---|---|---|
| `selfcheck` | Confirms n8n can reach the API and the API can reach the database. Writes nothing | Phase 0 |

Workflow exports (`*.json`) are added here from Phase 0.5, when the first recorder job exists.
