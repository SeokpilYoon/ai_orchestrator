# Product

A minimal todo list service that lets a single user create, list,
and complete tasks via a REST API.

## Target users

- Solo developers prototyping a backend service

## Functional requirements

- Add a task with a title (must)
  - POST /tasks accepts {"title": "..."} and returns 201
  - The task gets a unique integer id
- List all tasks (must)
  - GET /tasks returns the full task list as JSON
- Mark a task complete (should)
  - PATCH /tasks/{id} with {"done": true} flips the status
  - Returns 404 for unknown ids
- Delete a task (could)

## Non-functional requirements

- The service must respond within 200ms locally
- API documented in OpenAPI

## Constraints

- No external database — use an in-memory store
- Single language: Python 3.11

## Out of scope

- Authentication
- Multi-user support
