# n8n-ci-actions

Reusable GitHub Actions workflow + tooling to **validate and deploy n8n workflows** from a Git repository to a running n8n instance via the official Public API.

Designed to be consumed by multiple `n8n-*` repositories that hold n8n workflow JSONs as the source of truth.

## What it does

| Job | When it runs | What it does |
|---|---|---|
| `validate` | On every push and pull request | Lints every workflow JSON in the consumer repo (see below) |
| `deploy`   | On push to `main` (or `workflow_dispatch`) | Upserts every workflow into the target n8n instance via Public API |

### Validations (`validate_workflow.py`)

1. JSON parseable
2. Required top-level keys present (`name`, `nodes`, `connections`, `active`, `settings`)
3. `active: false` (activation is operator action, not Git)
4. `pinData` empty (no fixtures committed)
5. Every tag listed in `required-tags` present on the workflow
6. Sticky Note named `Git Source of Truth - Editing Notice` present in the canvas
7. File name == workflow `name`
8. No obvious secret patterns (JWT, OpenAI/AWS/Slack keys, password fields)
9. `credentials` blocks only carry `id` + `name` (no raw values)

### Deploy (`deploy_workflow.py`)

For each workflow JSON:
- Look up on n8n by `id`
- Exists → `PUT /api/v1/workflows/{id}` (update)
- Doesn't exist → `POST /api/v1/workflows` (create)
- After upsert: ensure tags via `PUT /api/v1/workflows/{id}/tags` (creating any missing tag)

Never activates a workflow. Never sets `parentFolderId` (the Public API does not expose folder placement — folder is a one-time manual setup; updates preserve it).

Settings keys not in the Public API allowlist are silently dropped before PUT/POST (the n8n Public API rejects unknown keys with `"request/body/settings must NOT have additional properties"`).

## How to consume from your `n8n-*` repo

Create `.github/workflows/n8n-sync.yml` in your repo:

```yaml
name: n8n Sync

on:
  push:
    branches: [main]
    paths:
      - "**/*.json"
      - ".github/workflows/n8n-sync.yml"
  pull_request:
    paths:
      - "**/*.json"
      - ".github/workflows/n8n-sync.yml"
  workflow_dispatch:
    inputs:
      dry_run:
        description: "Dry run (do not write to n8n)"
        type: boolean
        default: false

jobs:
  sync:
    uses: synapz-tech/n8n-ci-actions/.github/workflows/n8n-sync.yml@v1
    with:
      required-tags: "<your-domain-tag>,Git Source of Truth"
      dry-run: ${{ github.event.inputs.dry_run == 'true' }}
    secrets: inherit
```

Replace `<your-domain-tag>` with the tag every workflow in your repo must carry (e.g. one tag per domain).

## Required secrets / variables in the consumer

Set at the organization level (recommended) or per repo:

- **Variable** `N8N_API_URL` — base URL of your n8n instance (e.g. `https://n8n.example.com`)
- **Secret** `N8N_API_KEY` — long-lived n8n Public API key (n8n UI → Settings → n8n API → Create API Key)

For private consumer repos, the organization plan must be GitHub Team or higher for org-level secrets to be readable by them.

## Inputs

| Input | Type | Default | Description |
|---|---|---|---|
| `required-tags` | string | `"Git Source of Truth"` | Comma-separated tag names every workflow JSON must carry |
| `ref` | string | `"v1"` | Tag/branch of this repo to checkout for the tooling (pin for reproducibility) |
| `python-version` | string | `"3.11"` | Python version on the runner |
| `dry-run` | boolean | `false` | When `true`, the deploy job runs `--dry-run` and does not write to n8n |

## Versioning

Tags `vMAJOR` (`v1`, `v2`, ...) are rolling — they move forward with compatible features and fixes. Consumers pin with `@v1` to receive patches automatically. Breaking changes (renamed/removed inputs, schema changes) ship under a new MAJOR tag and consumers migrate explicitly.

## Local debugging

The scripts are stdlib-only Python. Run them against any workflow repo:

```bash
# Validate
N8N_WORKFLOWS_ROOT=/path/to/your/n8n-workflow-repo \
N8N_REQUIRED_TAGS="MyTag,Git Source of Truth" \
python3 scripts/n8n/validate_workflow.py --all

# Deploy dry-run
N8N_API_URL=https://n8n.example.com \
N8N_API_KEY=eyJ... \
N8N_WORKFLOWS_ROOT=/path/to/your/n8n-workflow-repo \
N8N_REQUIRED_TAGS="MyTag,Git Source of Truth" \
python3 scripts/n8n/deploy_workflow.py --all --dry-run
```

## Project conventions enforced

The validator and deploy script assume your workflow JSONs follow these conventions:

- One JSON file per workflow, file name == workflow `name`
- `active: false` (activation is controlled inside n8n)
- `pinData` empty
- Sticky note on the canvas named `Git Source of Truth - Editing Notice` reminding editors that Git is the source of truth
- A configurable set of required tags (`required-tags` input)
- Folder layout in the repo mirrors the folder tree inside the n8n project (subfolders → subfolders)

## License

[MIT](./LICENSE).
