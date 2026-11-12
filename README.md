# Artemis

![Artemis](app/static/artemis-icon.png)

**Private, local-first information architecture developed by CIN Entertainment.**

Artemis is a locally operated information environment that connects language-model reasoning with controlled retrieval, persistent storage, deterministic evidence handling, policy enforcement, auditing, approvals, and specialized information services.

Artemis is not a standalone language model and is not limited to a single autonomous agent. The language model is one component within a larger architecture responsible for coordinating information retrieval, analysis, memory, authorization, and user-facing operations.

## Current Release

**Version:** `0.1.0`  
**Release tag:** `v0.1.0`  
**Application port:** `8200`  
**Default host:** `127.0.0.1`  
**External access:** Disabled  
**Telemetry:** Disabled

The `v0.1.0` release establishes the operational foundation for future information collection, processing, analysis, reporting, and monitoring capabilities.

## Core Capabilities

Artemis currently provides:

- A private, localhost-only operating environment
- A unified browser-based Command Deck
- Persistent conversations and user preferences
- Local language-model integration through Ollama
- Semantic-memory infrastructure through Chroma
- Controlled retrieval from connected services
- Source-specific authorization and policy enforcement
- Approval workflows for controlled operations
- Audit and application-lifecycle records
- Encrypted handling of protected local values
- Service-health and storage-health monitoring
- Deterministic Market Lens fact presentation
- Clear separation between retrieved facts and model analysis
- One-click startup and shutdown
- Desktop shortcuts for normal operation
- Automated test coverage across the platform

## Connected Services

| Service | Purpose | Default address | Status |
|---|---|---:|---|
| Artemis | Core application and Command Deck | `127.0.0.1:8200` | Required |
| Ollama | Local language-model runtime | `127.0.0.1:11434` | Required for model responses |
| Chroma | Local semantic-memory service | `127.0.0.1:8100` | Required |
| Intel Notes Manager | Recorded information and note retrieval | `127.0.0.1:8001` | Optional |
| Protective Intelligence Risk Tracker | Case and risk-record retrieval | `127.0.0.1:8000` | Optional |
| Market Lens | Asset, price, analytics, and signal retrieval | `127.0.0.1:8003` | Optional |
| Odysseus Workspace | Transitional interface integration | `127.0.0.1:7000` | Optional |

Optional service outages do not prevent the core Artemis application from operating. Required service failures are reflected in the system-health status.

## Architecture Overview

Artemis is organized into several functional layers:

1. **Command Deck**
   - Provides the local browser interface.
   - Manages conversations, controlled retrieval options, and system status.

2. **API and Application Services**
   - Exposes conversation, preference, approval, health, and storage endpoints.
   - Coordinates application startup and shutdown.

3. **Orchestration**
   - Builds the model request.
   - Retrieves authorized supplemental information.
   - Separates deterministic facts from generated analysis.
   - Persists the completed conversation turn.

4. **Runtime Providers**
   - Define a shared model-provider contract.
   - Support local Ollama generation and deterministic testing.

5. **Controlled Retrieval**
   - Connects Artemis to approved local services.
   - Sanitizes retrieved fields.
   - Limits record counts and context length.
   - Treats retrieved content as untrusted reference data.

6. **Governance and Security**
   - Applies authorization policies.
   - Records audit events.
   - Supports approval-controlled operations.
   - Prevents external application access by default.

7. **Persistent Storage**
   - Stores conversations, messages, preferences, approvals, identities, and audit records.
   - Uses Chroma for local semantic-memory infrastructure.

Additional architecture details will be maintained in `docs/ARCHITECTURE.md`.

## Repository Structure

```text
artemis/
├── app/
│   ├── connectors/       External-service connector contracts
│   ├── routes/           FastAPI application routes
│   ├── runtime/          Model-provider contracts and runtimes
│   ├── services/         Business logic and orchestration
│   ├── static/           Command Deck interface and visual assets
│   ├── config.py         Environment configuration
│   ├── database.py       Database initialization and sessions
│   ├── main.py           FastAPI application entry point
│   ├── models.py         Persistent data models
│   ├── schemas.py        API request and response schemas
│   └── security.py       Application security configuration
├── data/
│   └── chroma/           Local Chroma persistence
├── scripts/
│   ├── Start-Artemis.ps1
│   ├── Stop-Artemis.ps1
│   └── Install-ArtemisShortcuts.ps1
├── tests/                Automated test suite
├── .env.example          Environment-variable template
├── requirements.txt      Python dependencies
└── README.md             Project overview