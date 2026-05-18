# n8n-ci-actions - Instructions for Agents

## Project description

This repository centralizes the CI/CD toolchain for repos that hold n8n workflow JSONs as the Git source of truth. It defines a reusable GitHub Actions workflow (`workflow_call`) plus two Python scripts (validate + deploy). Consumer repos call this reusable workflow instead of duplicating pipeline and scripts.

> This repo does NOT contain n8n workflows. Workflow JSONs live in the consumer repos (each one representing a folder inside an n8n project).

## Documentation hierarchy

- This `AGENTS.md` (root): universal rules (git flow, security, versioning).
- No submodules: the repo is technically small (one pipeline + two scripts), it does not need per-subfolder docs.

## Architecture

```
n8n-ci-actions/
├── .github/workflows/n8n-sync.yml   # Reusable workflow (workflow_call)
├── scripts/n8n/validate_workflow.py # Lint (env-configurable)
└── scripts/n8n/deploy_workflow.py   # Upsert via n8n Public API (env-configurable)
```

The scripts are parameterized via **environment variables**, not via CLI args or hardcoded constants:

- `N8N_REQUIRED_TAGS` — comma-separated list of tag names that must be on every workflow
- `N8N_WORKFLOWS_ROOT` — directory to scan (when the script runs in another repo via the reusable workflow)
- `N8N_API_URL` / `N8N_API_KEY` — used only by the deploy script

## Conventions

### Changes to the scripts

Every change to `scripts/n8n/*.py` affects ALL consumer repos. Treat it as shared infrastructure:

- No breaking changes within `vMAJOR` (consumers pin `@v1`)
- Test against at least two consumer repos before tagging a new version
- Document new behavior in `README.md`
- Consider a feature flag via env var when a change is opt-in

### Changes to the reusable workflow

`.github/workflows/n8n-sync.yml` has a public contract (`inputs`):

- Renaming an input → MAJOR (`v2`)
- Adding an optional input with a default → MINOR (stays on `v1`)
- Changing the default of an existing input → consider MAJOR if it can break consumers
- Removing existing steps (validate, deploy, check) → MAJOR

### Versioning

Tags `vMAJOR` (major only, for rolling latest):

- `v1` points to the last stable commit on the v1 line (moves forward with new features)
- Consumers use `@v1` and receive compatible fixes/features automatically
- Breaking change → create `v2`, announce in the CHANGELOG, migrate consumers one by one

Exact tags (`v1.0.0`, `v1.1.0`) are optional — use only if you need exact reproducibility.

### Adding a new validation

In `validate_workflow.py`:

1. Add the check inside `validate_file(path)`
2. Make the error message **actionable** (tell the user exactly what to change)
3. Document the new rule in `README.md` ("Validations")
4. Test against at least two consumer repos before merging
5. Consider a feature flag (`N8N_LINT_RULES=no-pinData,no-secrets`) if the new rule may break existing repos

### Adding a new n8n operation

In `deploy_workflow.py`:

1. Verify the operation exists in the **Public API** (`/api/v1/*`). Operations available only via `/rest` (folder placement, activation, etc.) stay out of scope here.
2. Filter the payload to only send fields the API accepts (see `ALLOWED_BODY_KEYS`, `ALLOWED_SETTINGS_KEYS`)
3. Handle idempotency: re-running the deploy should not produce different side effects
4. Document the behavior in `README.md`

## Git workflow

- Branch from `main`: `<type>/<short-description>` (e.g. `fix/strip-callerPolicy-from-settings`)
- Commits in English, Conventional Commits
- Suggested scopes: `ci`, `scripts`, `repo`, `docs`
- Examples:
  - `feat(ci): add dry-run input to reusable workflow`
  - `fix(scripts): allow new settings key in Public API allowlist`
  - `docs(repo): clarify how to debug locally`

## Security

- **Never** log values of `N8N_API_KEY` or any other secret
- **Never** call `/rest/*` (the internal API) from CI — it requires a browser session and does not work with an API key. Use only `/api/v1/*`
- **Never** assume the consumer repo is private — write the scripts thinking `N8N_WORKFLOWS_ROOT` can contain anything
- Validate input: `N8N_REQUIRED_TAGS` is split by comma without escaping; tags whose name contains a comma break. Documented as a known limitation.
- This repository is **public**. Do NOT commit any internal information, organization URLs, internal hostnames, or examples that reference a specific organization. All examples must be generic (`https://n8n.example.com`, `<your-domain-tag>`, etc.).

## How to test a change before merging

1. Push to a `feature/...` branch on this repo
2. In a consumer repo, temporarily change the `uses:` to point to the branch: `@feature/my-branch`
3. Push on the consumer to trigger the pipeline
4. Inspect the `validate` and `deploy` logs
5. Revert the `uses:` to `@v1` on the consumer
6. Merge the branch on this repo (`v1` tag will be moved manually after validation)

## Releasing a new version of v1

After merging changes to `main` that should be picked up by all consumers:

```bash
git checkout main && git pull
git tag -fa v1 -m "rolling v1: <short note>"
git push --force-with-lease origin v1
```

The moving tag updates every consumer pinned to `@v1` on the next workflow invocation.
