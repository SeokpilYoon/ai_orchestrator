# Task: Add `/health` endpoint

## Goal

Add a `GET /health` endpoint to the FastAPI application that returns
`{"status": "ok"}` with HTTP 200.

## Acceptance Criteria

- `GET /health` returns 200
- Response body is `{"status": "ok"}`
- A pytest test covers the endpoint
- Existing tests still pass

## Constraints

- Do not modify the database layer
- Do not add new dependencies
- Keep the change under ~30 lines

## Workflow recommendation

`feature`
