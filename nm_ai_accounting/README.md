# NM AI Accounting Agent

Production-oriented Tripletex competition agent with deterministic workflow routing.

## Architecture

Pipeline per request:

1. Intake (`/solve`): validate request and decode attachments.
2. Structured parsing: language + entities + task classification.
3. Select one primary task family.
4. Build minimal execution plan for that workflow.
5. Validate endpoint/query/fields before each HTTP call.
6. Execute with max one intelligent retry.
7. Return `{"status":"completed"}`.

Main folders:

- `models/`: typed request/task/plan models
- `parsing/`: language detect, normalization, attachment parsing, entity extraction
- `routing/`: task classifier and workflow registry
- `workflows/`: isolated workflow implementations
- `tripletex/`: client + request validators + field allowlists
- `execution/`: planner, executor, retry policy
- `tests/`: regression tests for routing and request validation

## Implemented workflows

- `create_customer`
- `create_employee`
- `create_product`
- `create_department`
- `create_project`
- `create_invoice`
- `order_to_invoice`
- `register_payment`
- `salary_transaction`

## Install

```powershell
cd nm_ai_accounting
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Configure env

Copy `.env.example` to `.env` and set:

- `TRIPLETEX_API_URL`
- `TRIPLETEX_SESSION_TOKEN`
- `SOLVE_API_KEY` (optional)
- `LOG_LEVEL`
- `TRIPLETEX_TIMEOUT_SECONDS`
- `MAX_INTELLIGENT_RETRIES`

## Run locally

```powershell
cd nm_ai_accounting
.venv\Scripts\python.exe -m uvicorn app:app --host 0.0.0.0 --port 8000
```

Health:

```powershell
curl http://localhost:8000/health
```

Solve test:

```powershell
curl -X POST http://localhost:8000/solve `
  -H "Content-Type: application/json" `
  -d "{\"prompt\":\"Opprett en kunde med navn Testkunde AS, test@example.org\",\"files\":[],\"tripletex_credentials\":{\"base_url\":\"https://kkpqfuj-amager.tripletex.dev/v2\",\"session_token\":\"YOUR_TOKEN\"}}"
```

## Run tests

```powershell
cd nm_ai_accounting
.venv\Scripts\python.exe -m pytest -q
```
