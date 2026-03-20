# NM AI Accounting Agent

Starter implementation for the NM Tripletex `/solve` endpoint contract.

## 1. Install

```powershell
cd nm_ai_accounting
..\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

If your venv is not set up, create one first:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 2. Configure env

Copy `.env.example` to `.env` and set values:

- `TRIPLETEX_API_URL`
- `TRIPLETEX_SESSION_TOKEN`
- `SOLVE_API_KEY` (optional, if you set API key in submission form)

## 3. Run locally

```powershell
cd nm_ai_accounting
..\.venv\Scripts\python.exe -m uvicorn app:app --host 0.0.0.0 --port 8000
```

Health check:

```powershell
curl http://localhost:8000/health
```

Solve contract test:

```powershell
curl -X POST http://localhost:8000/solve `
  -H "Content-Type: application/json" `
  -d "{\"prompt\":\"Opprett en kunde med navn Testkunde AS, test@example.org\",\"files\":[],\"tripletex_credentials\":{\"base_url\":\"https://kkpqfuj-amager.tripletex.dev/v2\",\"session_token\":\"YOUR_TOKEN\"}}"
```

## 4. Connect in dashboard

Set endpoint URL to your deployed `/solve`, for example:

- `https://your-domain.tld/solve`

If you set `SOLVE_API_KEY`, send:

```text
Authorization: Bearer <SOLVE_API_KEY>
```

## Notes

- Endpoint spec implemented:
  - Request: `prompt`, `files`, `tripletex_credentials`
  - Response: `{"status":"completed"}`
- Current solver supports first-pass "opprett X" tasks for:
  - `employee`, `customer`, `product`, `department`, `project`
- This should be extended with more robust prompt interpretation and multi-step workflows.
