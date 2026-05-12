# Release checklist

This checklist captures every manual step required to cut a public
release of `devforge` to PyPI. The repo is **prepared but not
published**: building the wheel, validating it, and writing the
release notes are automated; the actual `twine upload` is gated on the
credential and audit steps near the end.

## 0. Pre-release sanity (every release)

- [ ] `git status` is clean. No untracked files outside `.orchestrator/`.
- [ ] `git pull --ff-only` against `origin/main`.
- [ ] `.venv/bin/pytest -q` → **0 failures**, real_provider tests
      explicitly skipped (or run with `-m real_provider` if the
      release should be smoked against the real CLIs).
- [ ] `.venv/bin/ruff check devforge tests` → **All checks passed**.
- [ ] `.venv/bin/mypy devforge` → **0 errors** under both the default
      and strict-overlay profiles (see `docs/TYPING.md`).
- [ ] All recently shipped workflows + providers documented in
      `docs/ROADMAP.md` and `docs/WORKFLOWS.md`.

## 1. Version + changelog

- [ ] Bump `[project] version` in `pyproject.toml`. Follow SemVer:
      patch for fixes, minor for additive features, major for any
      breaking config / workflow schema change.
- [ ] Update `CHANGELOG.md`. Group entries by Added / Changed / Fixed /
      Removed. Reference the DEVF id where applicable (the project
      convention).
- [ ] Update `docs/RELEASE_NOTES.md` with the user-facing summary
      (install command, verification matrix, known limitations).
- [ ] Commit the bump separately so the wheel artifact carries an
      unambiguous version: `git commit -m "release: vX.Y.Z"`.

## 2. Project URLs

The `[project.urls]` block in `pyproject.toml` is intentionally
**empty** in this repo. PyPI's project page is meaningless without
real URLs, so before the first publish:

- [ ] Decide the canonical Git host + slug.
- [ ] Add the `[project.urls]` block with `Repository`,
      `Documentation`, `Changelog`, `Issues`.
- [ ] Add a `Source` URL pointing at a stable release tag.

## 3. Build the distributions

- [ ] Clean any prior artifacts:
      `rm -rf dist build *.egg-info`
- [ ] Build a wheel + sdist. The repo's `[build-system]` already
      depends on `setuptools>=68 / wheel`, so either of these works:

      ```bash
      # Option A — the standalone build frontend (recommended):
      python -m pip install --upgrade build
      python -m build .

      # Option B — pip's wheel command (already verified in this repo):
      python -m pip wheel . --no-deps -w dist
      python -m pip sdist .   # not built-in; prefer Option A for sdist.
      ```

- [ ] Inspect the wheel manifest. Every workflow YAML, every
      `dashboard/static/*`, every role prompt, and every project
      profile must be present:

      ```bash
      python -m zipfile -l dist/devforge-*.whl | grep -E "yaml|static|prompts|profiles"
      ```

## 4. Validate the wheel

- [ ] **Importability** — install into a fresh venv and import every
      public entry point. The wheel-import smoke used during DEVF-Wave
      5 preparation:

      ```bash
      python -m venv /tmp/devforge-check
      /tmp/devforge-check/bin/pip install dist/devforge-*.whl
      /tmp/devforge-check/bin/python -c "
      from devforge.core.workflow_engine import load_workflow
      for wf in ('feature', 'bugfix', 'refactor',
                 'code_review_only', 'research_optimize',
                 'app_from_prd'):
          load_workflow(wf)
      from devforge.providers.openai_api import OpenAiApiProvider
      from devforge.providers.claude_agent_sdk import ClaudeAgentSdkProvider
      print('OK')
      "
      ```

- [ ] **Console script** — confirm `devforge --version` prints the
      version you just bumped:

      ```bash
      /tmp/devforge-check/bin/devforge --version
      ```

- [ ] **Metadata** — run `twine check` (recommended once `twine` is
      installed):

      ```bash
      python -m pip install --upgrade twine
      python -m twine check dist/*
      ```

      The check must report `PASSED` for every artifact. If
      `twine` is not available, the metadata can be eyeballed via
      `pkginfo dist/devforge-*.whl`.

- [ ] **Optional extras** — verify each extra installs cleanly:

      ```bash
      /tmp/devforge-check/bin/pip install "devforge[dashboard]" --find-links dist
      /tmp/devforge-check/bin/pip install "devforge[providers-openai]" --find-links dist
      /tmp/devforge-check/bin/pip install "devforge[providers-anthropic]" --find-links dist
      ```

## 5. Tag

- [ ] `git tag -a vX.Y.Z -m "devforge vX.Y.Z"`.
- [ ] **Do not** `git push --tags` until publish is approved — that
      step is downstream of credential and audit checks.

## 6. Publish to TestPyPI (recommended before production)

- [ ] Configure TestPyPI credentials via `~/.pypirc` or
      `TWINE_USERNAME=__token__` + `TWINE_PASSWORD=<TestPyPI-token>`.
- [ ] Upload:

      ```bash
      python -m twine upload --repository testpypi dist/*
      ```

- [ ] Install from TestPyPI into a fresh venv and re-run the importability
      smoke from step 4.

## 7. Publish to PyPI (manual approval required)

> **Deferred external requirement** — devforge does **not** run
> `twine upload` automatically. The actual publish is intentionally
> gated on a human review of the wheel contents + credentials.

- [ ] Confirm the PyPI project (`devforge`) exists OR is reserved by
      the intended maintainer account.
- [ ] Generate a project-scoped PyPI token at
      `https://pypi.org/manage/account/token/`.
- [ ] Export credentials and upload:

      ```bash
      python -m twine upload dist/*
      ```

- [ ] Browse to the published page and confirm:
      - the description renders from `README.md`
      - the project links resolve (added in step 2)
      - the optional extras list matches `pyproject.toml`
      - every workflow YAML / dashboard asset is visible in the
        "Files" view of the wheel.

## 8. Post-publish

- [ ] Push the release tag: `git push origin vX.Y.Z`.
- [ ] Push `main`: `git push origin main`.
- [ ] Announce in the project's changelog channel.
- [ ] Open the next version's `## Unreleased` heading in
      `CHANGELOG.md`.

## Deferred requirements (NOT auto-completed)

These steps require credentials or human approval. They are
deliberately left as deferred so devforge never publishes on the
user's behalf:

| Step | Reason |
|---|---|
| Set `[project.urls]` | Public repository slug is not finalised. |
| TestPyPI token | Credential. |
| PyPI token | Credential + irreversible publish action. |
| `git push origin vX.Y.Z` | Visible to others; outside the autonomy contract. |
| `git push origin main` | Same. |

devforge's autonomous-mode `chore(release)` prep verifies that:

- the wheel builds,
- the wheel imports cleanly in a fresh venv,
- every workflow YAML + dashboard asset is included,
- pytest / ruff / mypy are green at the time of build.

The remaining steps are explicit human actions.
