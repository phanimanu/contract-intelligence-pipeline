# Architecture Decision Note: Contract Intelligence Pipeline

## 1. Context and Medallion Design
This platform processes critical legal agreements for a major Architecture & Engineering (A/E) firm. Because contracts dictate financial liability and insurance compliance, data reliability and traceability are paramount. We adopted a **Medallion Architecture (Bronze → Silver → Gold)** layout:
- **Bronze (Raw Ingestion)**: Ingestion Lands API batch payloads as-is, encapsulated in a metadata envelope containing the ingestion date and source details. It is structured using Hive-style partitioning (e.g., `year=YYYY/month=MM/day=DD/`). Keeping the raw data completely unmodified ensures we can re-ingest and re-process records if downstream logic changes.
- **Silver (Cleaned & Validated)**: Downstream processes (simulated in Phase 2) read from Bronze, validate boundaries via guardrails, and normalize schema variations (e.g., merging `clause_type` and `category` fields into a unified `clause_category` property).
- **Gold (Aggregated & Structured)**: The final validated classifications, risk assessments, and human-in-the-loop decisions are aggregated into a structured, production-ready transaction ledger (`audit_log.json`).

## 2. Ingestion Resilience (Phase 1 Decisions)
- **Schema Drift Handling**: The API versioning is unstable (v2.1 to v2.3 shift). Instead of blocking raw ingestion when fields change (which disrupts operations), we detect and alert on changes. The ingestor compares incoming payloads against `schema_baseline.json`. If discrepancies (renamed fields, deleted fields, or new nested objects) are identified, it generates a timestamped drift report (`drift_report_*.json`) and logs warning flags, allowing the raw data to land safely while alerting data engineers.
- **Checkpoint-Based Resumability**: To prevent reprocessing raw datasets in case of network or worker node failures, `ingest.py` tracks processed `clause_id`s in `ingestion_state.json`. If interrupted, a re-run skips already ingested IDs, ensuring exact-once ingestion processing.

## 3. Multi-Agent Orchestration & Guardrails (Phase 2 Decisions)
- **Role-Based Agent Design**: Rather than relying on a single complex prompt (which suffers from context dilution and poor formatting reliability), we divided the legal analysis into specialized agents:
  - **Clause Classifier Agent**: Focused solely on high-accuracy domain categorization.
  - **Risk Flagging Agent**: Evaluates the specific category using professional A/E risk principles (e.g. uninsurable broad indemnification, retainage withholding).
  - **Audit & Learning Agent**: Responsible for structured ledger writing, HITL metadata compilation, and staging overrides.
- **Provider Interface Pattern**: To ensure maximum testability, we abstracted the LLM client using an `LLMProvider` interface. The system uses a mock provider by default for zero-cost, offline verification, but instantly plugs in Google's Generative AI SDK (`gemini-1.5-flash`) when the `GEMINI_API_KEY` is present.
- **Active Guardrails**:
  - **Scope Boundary Guardrail**: Prior to querying LLM endpoints, the validator scans clauses for length and non-legal content (like code snippets or cooking recipes), preventing token waste and potential prompt injections.
  - **Token/Cost Budget Limit**: Tracks character-to-token estimates in real-time, raising a fatal exception if the budget ceiling is breached to prevent runaway API fees.

## 4. Human-In-The-Loop Boundary
To meet standard regulatory requirements, the system is designed to **never modify its own parameters, prompts, or weights automatically**. When reviews return low confidence or high-risk flags, the pipeline prompts for human approval (or stages it in a corrections queue). Staged corrections are stored in a dedicated corrections file. These can then be reviewed by human administrators and consumed in batch offline runs for supervised fine-tuning or prompt engineering updates.

## 5. Architectural Tradeoffs & Next Steps
- **Tradeoff**: Running Python local I/O rather than a distributed engine (e.g., PySpark) keeps the footprint low and developer setup trivial, which was preferred for this POC.
- **Next Steps for Production**:
  - Migrate the state and schemas from JSON files to a relational database (e.g., PostgreSQL) or metadata tables (e.g., Delta Lake).
  - Wrap the CLI scripts in an orchestration framework (like Apache Airflow) to automate daily runs and trigger slack alerts on schema drift.
  - Integrate a vector database (RAG) to dynamically feed context-appropriate, client-specific historical contract guidelines to the Risk Agent.
