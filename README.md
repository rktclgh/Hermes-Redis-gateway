# Hermes Redis Gateway

Redis-backed HTTP gateway and worker pool for running Hermes CLI one-shot inference safely from multiple local services.

The gateway is designed for small private infrastructure where several applications need AI generation calls, but direct concurrent Hermes CLI execution would be hard to control. Hermes itself is treated as an external executable. This project does not patch or vendor Hermes.

## What It Does

- Exposes a synchronous compatibility endpoint: `POST /generate`
- Exposes async job endpoints: `POST /jobs`, `GET /jobs/{jobId}`
- Uses Redis Streams for durable job coordination
- Uses Redis slot leases to cap global Hermes concurrency
- Runs each Hermes subprocess with slot-specific `HERMES_HOME`, profile, and working directory
- Supports multiple services sharing the same gateway
- Keeps prompt payloads out of normal job status responses

## Architecture

```text
Local services
  -> Hermes Redis Gateway API
    -> Redis Stream + job hashes + slot leases
      -> Worker threads
        -> Hermes CLI one-shot subprocesses
```

The default slot count is `10`. That means many jobs may queue, but only ten Hermes subprocesses can run at the same time across all workers sharing the same Redis.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip setuptools wheel
pip install -e ".[dev]"
cp .env.example .env
```

Edit `.env` for your Redis URL and Hermes paths.

## Configuration

Important environment variables:

| Variable | Default | Description |
| --- | --- | --- |
| `HRG_REDIS_URL` | `redis://127.0.0.1:6379/0` | Redis connection URL |
| `HRG_HOST` | `127.0.0.1` | API bind host |
| `HRG_PORT` | `8788` | API port |
| `HRG_API_KEY` | empty | Bearer token. Required when binding outside localhost |
| `HRG_SLOT_COUNT` | `10` | Max global concurrent Hermes runs |
| `HRG_WORKER_THREADS` | `10` | Worker threads in one worker process |
| `HRG_QUEUE_MAX_SIZE` | `100` | Max active queued/pending backlog before new jobs are rejected |
| `HRG_QUEUE_COUNT_KEY` | `hermes:queue:default:count` | Atomic active backlog counter |
| `HRG_HERMES_PYTHON` | `/home/song/.hermes/hermes-agent/venv/bin/python` | Python executable that can run Hermes |
| `HRG_HERMES_PROVIDER` | `openai-codex` | Hermes provider |
| `HRG_HERMES_MODEL` | `gpt-5.4-mini` | Default model |
| `HRG_HERMES_TOOLSETS` | empty | Optional comma-separated Hermes toolsets passed to oneshot |
| `HRG_ALLOWED_MODELS` | `gpt-5.4-mini,gpt-5.4` | Model allowlist |
| `HRG_MAX_PROMPT_BYTES` | `200000` | Prompt/request size guard |

Security default: `HRG_HOST=127.0.0.1`. If you bind to `0.0.0.0`, set `HRG_API_KEY`.

## Run

Start the API:

```bash
source .venv/bin/activate
set -a
source .env
set +a
hermes-redis-gateway-api
```

Start workers in another process:

```bash
source .venv/bin/activate
set -a
source .env
set +a
hermes-redis-gateway-worker
```

## API

### Health

```bash
curl -s http://127.0.0.1:8788/health
```

### Synchronous Generate

```bash
curl -s http://127.0.0.1:8788/generate \
  -H "Content-Type: application/json" \
  -H "X-HRG-Service: vlainter-be" \
  -d '{"prompt":"Return JSON with one interview question.","model":"gpt-5.4-mini"}'
```

The sync endpoint still creates a Redis job internally, then waits for the result. If the wait timeout expires before Hermes finishes, it returns `202` with a `jobId`.

### Async Job

```bash
curl -s http://127.0.0.1:8788/jobs \
  -H "Content-Type: application/json" \
  -H "X-HRG-Service: vlainter-be" \
  -d '{"prompt":"Return JSON with one interview question."}'
```

Poll:

```bash
curl -s http://127.0.0.1:8788/jobs/<jobId>
```

Normal job status responses do not include the original prompt.

## Redis Keys

Defaults:

```text
hermes:stream:default
hermes:queue:default:count
hermes:job:{jobId}
hermes:slot:{1..10}
```

Redis Streams are used so worker-owned jobs can be recovered through pending-entry reclaim behavior. A separate Redis counter tracks active queued/pending backlog because acknowledged Stream entries may remain in the Stream history. Slot leases use compare-and-delete semantics so one worker cannot release another worker's slot.

## Failure Behavior

- Redis unavailable: API returns `503`
- Queue full: API returns `429`
- Hermes timeout: job becomes `TIMEOUT`
- Hermes non-zero exit: job becomes `FAILED`
- Slot lease lost: worker terminates the Hermes subprocess and prevents success write
- Slot unavailable after Stream consume: worker requeues the message without changing backlog count
- Pending reclaim waits longer than Hermes timeout to avoid duplicate execution while a long subprocess is still alive
- Sync wait timeout: `/generate` returns `202`; caller can poll `/jobs/{jobId}`

Prompt privacy note: the worker writes each prompt to a slot-local `0600` temporary file and invokes a small bridge script that calls `hermes_cli.oneshot.run_oneshot()`. The prompt body is not placed in the subprocess argv.

## Development

```bash
pip install -e ".[dev]"
pytest
```

The tests use fakes where possible. Integration tests against a real Redis and Hermes runtime should be added before production use beyond a local private server.

## Deployment Notes

Recommended deployment shape:

- one API process
- one worker process
- local Redis
- API bound to localhost
- reverse proxy only if another host must call it
- bearer auth if exposed beyond localhost

The worker can be scaled horizontally, but all workers must share the same Redis and slot prefix so the global concurrency cap is honored.

Systemd templates live in `deploy/systemd/`. Adjust `User`, `Group`, `WorkingDirectory`, `EnvironmentFile`, and `ExecStart` paths before installing them on a server:

```bash
sudo cp deploy/systemd/hermes-redis-gateway-*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now hermes-redis-gateway-api hermes-redis-gateway-worker
```

## Release Checklist

- `pytest` passes
- `.env` exists only on the server and is not committed
- Redis is reachable from API and worker
- `HRG_SLOT_COUNT` equals the intended global Hermes concurrency
- `HRG_HOST=127.0.0.1` unless there is a reverse proxy or explicit API key boundary
- `GET /health` reports Redis, queue backlog, and slot state
- At least one real `/generate` smoke test succeeds through Hermes
