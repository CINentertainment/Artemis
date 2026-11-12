# Artemis Development Roadmap

## Mission

Artemis will become a private, local-first information architecture capable of collecting authorized information, preserving evidence, organizing records, identifying relationships, producing traceable analysis, and delivering useful information products.

The objective is not unrestricted autonomy.

The objective is dependable, governed, and measurable information processing.

## Current Foundation

### Version 0.1.0

Completed:

- [x] Local FastAPI application
- [x] Artemis Command Deck
- [x] Local Ollama integration
- [x] Chroma semantic-memory service
- [x] Persistent conversations
- [x] Persistent preferences
- [x] Approval workflows
- [x] Authorization policies
- [x] Audit records
- [x] Application lifecycle records
- [x] Intel Notes integration
- [x] Risk Tracker integration
- [x] Market Lens integration
- [x] Controlled retrieval
- [x] Deterministic Market Lens facts
- [x] Separation of facts and model analysis
- [x] Service-health monitoring
- [x] Storage-health monitoring
- [x] Localhost-only service configuration
- [x] One-click startup and shutdown
- [x] Desktop shortcuts
- [x] Automated test suite
- [x] Release tag
- [x] Architecture documentation
- [x] Backup and recovery documentation
- [x] Automated backup utility
- [x] Automated system smoke test

## Phase 1 — Information Ingestion

### Objective

Create a consistent, traceable method for adding information to Artemis.

### Deliverables

- [ ] Universal information-record model
- [ ] Source Registry
- [ ] Source-type definitions
- [ ] Manual text ingestion
- [ ] Document ingestion
- [ ] Approved URL ingestion
- [ ] Original-content preservation
- [ ] Retrieval timestamps
- [ ] Content hashing
- [ ] Duplicate detection
- [ ] Collection status
- [ ] Collection error records
- [ ] Information-ingestion API
- [ ] Ingestion tests

### Exit Criteria

Phase 1 is complete when:

- Artemis can ingest at least 100 records.
- Every record retains its original source.
- Every record has a retrieval timestamp.
- Duplicate content is identified.
- Failed ingestion attempts are recorded.
- Stored information can be retrieved without model involvement.
- All ingestion tests pass.

## Phase 2 — Information Processing

### Objective

Convert collected material into structured and searchable information.

### Deliverables

- [ ] Text normalization
- [ ] Metadata extraction
- [ ] Date extraction
- [ ] Location extraction
- [ ] Entity extraction
- [ ] Topic categorization
- [ ] Relationship identification
- [ ] Relevance scoring
- [ ] Reliability fields
- [ ] Credibility fields
- [ ] Cross-source comparison
- [ ] Conflict detection
- [ ] Structured note generation
- [ ] Processing audit records
- [ ] Processing tests

### Exit Criteria

Phase 2 is complete when:

- The same entity can be recognized across multiple records.
- Conflicting claims are visibly flagged.
- Generated summaries remain linked to source records.
- Source content and model inference remain separate.
- Processing can be repeated without losing provenance.

## Phase 3 — Monitoring and Watchlists

### Objective

Allow Artemis to monitor defined information requirements on a schedule.

### Deliverables

- [ ] Information requirements
- [ ] Topic watchlists
- [ ] Entity watchlists
- [ ] Scheduled collection jobs
- [ ] Background processing
- [ ] Change detection
- [ ] Threshold-based alerts
- [ ] Failure retries
- [ ] Pause and resume controls
- [ ] Collection history
- [ ] Approval gates
- [ ] Monitoring tests

### Exit Criteria

Phase 3 is complete when:

- Artemis can complete a scheduled daily collection cycle.
- New information is separated from existing information.
- Relevant changes can generate alerts.
- Failed jobs do not corrupt stored information.
- Every scheduled action is auditable.

## Phase 4 — Analysis and Reporting

### Objective

Produce traceable, reusable information products.

### Deliverables

- [ ] Daily monitoring summary
- [ ] Weekly information brief
- [ ] Entity profile
- [ ] Event chronology
- [ ] Source comparison
- [ ] Risk summary
- [ ] Market summary
- [ ] Key findings
- [ ] Information gaps
- [ ] Confidence statements
- [ ] Source citations
- [ ] Word export
- [ ] PDF export
- [ ] Structured-data export
- [ ] Reporting tests

### Exit Criteria

Phase 4 is complete when:

- Every material claim can be traced to evidence.
- Retrieved facts are separated from calculated results.
- Calculated results are separated from model analysis.
- Reports identify missing or conflicting information.
- Reports can be regenerated consistently.

## Phase 5 — Operational Command Deck

### Objective

Support the complete information lifecycle through the Artemis dashboard.

### Deliverables

- [ ] Source Registry interface
- [ ] Ingestion interface
- [ ] Information-requirements panel
- [ ] Watchlist management
- [ ] Collection activity feed
- [ ] Entity pages
- [ ] Topic pages
- [ ] Alerts queue
- [ ] Review queue
- [ ] Source-health indicators
- [ ] Processing status
- [ ] Evidence viewer
- [ ] Report library
- [ ] Approval center
- [ ] Audit-history viewer

### Exit Criteria

Phase 5 is complete when:

- Normal operations no longer require direct API calls.
- Collection and processing status are visible.
- Evidence can be inspected from the dashboard.
- Reports can be created and reviewed through the interface.
- Artemis clearly shows what is known, inferred, conflicting, or missing.

## Phase 6 — Limited Pilot

### Objective

Prove that Artemis can produce measurable external value through a narrowly defined information product.

### Deliverables

- [ ] Select one lawful public-information use case
- [ ] Define one target customer type
- [ ] Define one recurring customer problem
- [ ] Create three sample products
- [ ] Measure production time
- [ ] Measure operating cost
- [ ] Identify ten qualified prospects
- [ ] Conduct a limited pilot
- [ ] Gather structured feedback
- [ ] Establish pilot pricing
- [ ] Document delivery procedures

### Exit Criteria

Phase 6 is complete when:

- One external user receives recurring value.
- Product scope is clearly defined.
- Delivery cost and production time are measured.
- Feedback produces measurable improvements.
- At least one customer demonstrates willingness to pay.

## Development Priorities

Features should be prioritized in this order:

1. Provenance
2. Reliability
3. Recoverability
4. Security
5. Usability
6. Automation
7. Scale

## Features Deferred Until Later

The following are intentionally deferred:

- Multi-user cloud hosting
- Public internet exposure
- Mobile applications
- Unrestricted autonomous actions
- Large numbers of collection connectors
- Customer billing
- Complicated agent swarms
- Expensive hardware expansion
- General collection without defined requirements

## Near-Term Build Order

The immediate build sequence is:

1. Define the universal information-record model.
2. Define the Source Registry model.
3. Add database migrations or initialization support.
4. Implement source-management services.
5. Add source-management API routes.
6. Implement manual text ingestion.
7. Add hashing and duplicate detection.
8. Preserve original source content.
9. Add ingestion audit records.
10. Add comprehensive tests.

## Success Measure

The first major operational milestone is:

> Artemis can accept authorized information, preserve its original source, identify duplicate content, retrieve stored records, and show exactly where every stored fact originated.

The first commercial milestone is:

> One trustworthy information pipeline, one repeatable information product, and one paid pilot.