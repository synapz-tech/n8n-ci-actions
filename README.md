<a id="readme-top"></a>

<!-- PROJECT LOGO -->
<br />
<div align="center">
  <a href="https://synapz.tech">
    <img src="main/branding/favicon-96x96.png" alt="Synapz" width="80" height="80">
  </a>

  <h3 align="center">n8n CI Actions</h3>

  <p align="center">
    Reusable GitHub Actions workflow + tooling to validate and deploy n8n workflows from Git via the Public API.
    <br />
    <a href="https://github.com/synapz-tech/n8n-ci-actions/issues">Reportar Bug</a>
    ·
    <a href="https://github.com/synapz-tech/n8n-ci-actions/issues">Sugerir Feature</a>
  </p>
</div>

<!-- TABLE OF CONTENTS -->
<details>
  <summary>Sumário</summary>
  <ol>
    <li>
      <a href="#about-the-project">Sobre o Projeto</a>
      <ul>
        <li><a href="#built-with">Stack</a></li>
      </ul>
    </li>
    <li>
      <a href="#getting-started">Primeiros Passos</a>
      <ul>
        <li><a href="#prerequisites">Pré-requisitos</a></li>
        <li><a href="#installation">Instalação</a></li>
      </ul>
    </li>
    <li><a href="#usage">Uso</a></li>
    <li><a href="#contributing">Contribuindo</a></li>
    <li><a href="#license">Licença</a></li>
    <li><a href="#contact">Contato</a></li>
  </ol>
</details>

<!-- ABOUT THE PROJECT -->

## About The Project

O **n8n CI Actions** é o repositório central de CI/CD da Synapz para repos que mantêm workflows n8n como **fonte de verdade no Git**. Ele fornece um workflow reutilizável do GitHub Actions (`workflow_call`) e dois scripts Python padrão-lib para:

- **Validar** workflows n8n em cada push/pull request (lint de JSON, tags obrigatórias, ausência de segredos, etc.).
- **Deployar** workflows para uma instância n8n via **Public API** (`/api/v1/*`), garantindo que o estado em runtime reflita o que está commitado.

Funcionalidades principais:

- Workflow reusável `.github/workflows/n8n-sync.yml` consumido por repos `n8n-*` da organização.
- Validação local e em CI via `scripts/n8n/validate_workflow.py`.
- Deploy idempotente via `scripts/n8n/deploy_workflow.py` (upsert por `id` ou reconciliação por `name`).
- Reconciliação do estado `active` do workflow contra o valor commitado no JSON.
- Sem chamadas à API interna `/rest/*` — apenas endpoints documentados da Public API.

A arquitetura de alto nível é composta por:

| Camada | Componente | Descrição |
|--------|------------|-----------|
| CI/CD | `.github/workflows/n8n-sync.yml` | Workflow reusável chamado pelos repos de workflows |
| Validação | `validate_workflow.py` | Lint de JSONs de workflow antes do deploy |
| Deploy | `deploy_workflow.py` | Upsert de workflows e tags via n8n Public API |
| Consumidores | Repos `n8n-*` | Repositórios que armazenam os JSONs dos workflows |

<p align="right">(<a href="#readme-top">voltar ao topo</a>)</p>

### Built With

- [n8n](https://n8n.io/)
- [GitHub Actions](https://github.com/features/actions)
- [Python 3.11+](https://www.python.org/)
- [n8n Public API](https://docs.n8n.io/api/)

<p align="right">(<a href="#readme-top">voltar ao topo</a>)</p>

<!-- GETTING STARTED -->

## Getting Started

Para usar este repo como base de CI/CD dos seus workflows n8n, siga os passos abaixo.

### Prerequisites

- Uma instância n8n com a **Public API** habilitada.
- Um **n8n API Key** longo (n8n UI → Settings → n8n API → Create API Key).
- Repositórios consumidores (`n8n-*`) com permissão para chamar workflows reusáveis da organização.
- Python 3.11+ (apenas para execução local dos scripts).

### Installation

1. Clone o repositório:

   ```sh
   git clone https://github.com/synapz-tech/n8n-ci-actions.git
   cd n8n-ci-actions
   ```

2. (Opcional) Inspecione os scripts de validação e deploy:

   ```sh
   cat scripts/n8n/validate_workflow.py
   cat scripts/n8n/deploy_workflow.py
   ```

3. Nos repos consumidores, crie `.github/workflows/n8n-sync.yml` apontando para este workflow reusável:

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

> **Nota:** em produção o workflow `n8n-sync.yml` roda automaticamente nos repos consumidores em pushes para a branch `main`.

<p align="right">(<a href="#readme-top">voltar ao topo</a>)</p>

<!-- USAGE EXAMPLES -->

## Usage

### Estrutura do Projeto

```
.
├── README.md                        # Este arquivo
├── LICENSE                          # Licença MIT
├── .github/
│   └── workflows/
│       └── n8n-sync.yml             # Workflow reusável (workflow_call)
└── scripts/n8n/
    ├── validate_workflow.py         # Validação de JSONs de workflow
    └── deploy_workflow.py           # Deploy via n8n Public API
```

### Validations

O job `validate` roda em todo push e pull request. O script `validate_workflow.py` verifica:

1. JSON parseável.
2. Chaves top-level obrigatórias presentes (`name`, `nodes`, `connections`, `active`, `settings`).
3. `active` é booleano.
4. `pinData` vazio (sem fixtures commitados).
5. Todas as tags listadas em `required-tags` estão presentes no workflow.
6. Sticky Note nomeado `Git Source of Truth - Editing Notice` presente no canvas.
7. Nome do arquivo == `name` do workflow.
8. Ausência de padrões óbvios de segredo (JWT, OpenAI/AWS/Slack keys, campos de password).
9. Blocos `credentials` contendo apenas `id` + `name` (sem valores raw).

### Deploy

O job `deploy` roda em push para `main` (ou `workflow_dispatch`). Para cada workflow JSON:

- Busca no n8n por `id`.
- Existe → `PUT /api/v1/workflows/{id}` (update).
- Não existe e reconciliação está habilitada (padrão) → busca por `name`:
  - Exatamente 1 match → `PUT` para aquele id (com warning para atualizar o campo `id` do JSON).
  - 2+ matches → falha listando ids duplicados.
  - 0 matches → `POST /api/v1/workflows` (create).
- Não existe e reconciliação desabilitada (`N8N_RECONCILE_BY_NAME=0`) → `POST` (comportamento legado).
- Após upsert: garante as tags via `PUT /api/v1/workflows/{id}/tags`.
- Após tags: reconcilia o estado de runtime. `active: true` → `POST /api/v1/workflows/{id}/activate`. `active: false` → `POST /api/v1/workflows/{id}/deactivate`.

> A reconciliação por nome evita que o deploy crie duplicatas silenciosamente quando alguém recria um workflow no n8n com um novo id. Está habilitada por padrão — defina `N8N_RECONCILE_BY_NAME=0` para desabilitar.

### Inputs do Workflow Reusável

| Input | Type | Default | Description |
|---|---|---|---|
| `required-tags` | string | `"Git Source of Truth"` | Tags obrigatórias separadas por vírgula |
| `ref` | string | `"v1"` | Tag/branch deste repo a ser usada |
| `python-version` | string | `"3.11"` | Versão do Python no runner |
| `dry-run` | boolean | `false` | Quando `true`, não escreve no n8n |

### Variáveis de Ambiente Essenciais

Configure no consumer como secrets/variables da organização ou do repo:

```bash
# Variável
N8N_API_URL=https://n8n.example.com

# Secret
N8N_API_KEY=<n8n-api-key>
```

### Variáveis de Ambiente Opcionais

```bash
# 1 (padrão) ou 0. Habilita reconciliação por nome quando o id do JSON não existe.
N8N_RECONCILE_BY_NAME=1

# 1 (padrão) ou 0. Habilita reconciliação do estado active do workflow.
N8N_DEPLOY_ACTIVE=1
```

### Local Debugging

Os scripts usam apenas a biblioteca padrão do Python. Para rodar localmente:

```bash
# Validar
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

### Convenções de Projeto

O validator e o deploy script assumem que os JSONs de workflow seguem estas convenções:

- Um JSON por workflow, nome do arquivo == `name` do workflow.
- `active: false` (a ativação é controlada no n8n, mas o deploy pode reconciliar).
- `pinData` vazio.
- Sticky note no canvas nomeado `Git Source of Truth - Editing Notice`.
- Tags obrigatórias configuráveis via input `required-tags`.
- Layout de pastas no repo espelha a árvore de pastas dentro do projeto n8n.

### Versionamento

Tags `vMAJOR` (`v1`, `v2`, ...) são rolling — avançam com features e fixes compatíveis. Consumidores usam `@v1` para receber patches automaticamente. Mudanças breaking (inputs renomeados/removidos, mudanças de schema) sobem para uma nova tag MAJOR e os consumidores migram explicitamente.

### Documentação Adicional

- [Documentação do n8n](https://docs.n8n.io/)
- [n8n Public API Reference](https://docs.n8n.io/api/)
- [GitHub Actions Reusable Workflows](https://docs.github.com/en/actions/using-workflows/reusing-workflows)

<p align="right">(<a href="#readme-top">voltar ao topo</a>)</p>

<!-- CONTRIBUTING -->

## Contributing

Contribuições seguem o fluxo do repositório:

1. Crie uma branch a partir da `main` seguindo o padrão:

   ```text
   <tipo>/<descricao-curta>
   ```

   Exemplo: `docs/standardize-readme`

2. Faça commits em inglês e no padrão [Conventional Commits](https://www.conventionalcommits.org/):

   ```text
   docs(readme): standardize n8n readme
   ```

3. Abra um Pull Request para `main`.

> **Atenção:** todo change em `scripts/n8n/*.py` afeta todos os repos consumidores. Trate como infraestrutura compartilhada; teste contra pelo menos dois repos consumidores antes de mergear.

<p align="right">(<a href="#readme-top">voltar ao topo</a>)</p>

<!-- LICENSE -->

## License

Distribuído sob a licença MIT. Veja [`LICENSE`](./LICENSE) para mais informações.

<p align="right">(<a href="#readme-top">voltar ao topo</a>)</p>

<!-- CONTACT -->

## Contact

Synapz Engineering — [https://synapz.tech](https://synapz.tech)

Repositório: [https://github.com/synapz-tech/n8n-ci-actions](https://github.com/synapz-tech/n8n-ci-actions)

<p align="right">(<a href="#readme-top">voltar ao topo</a>)</p>
