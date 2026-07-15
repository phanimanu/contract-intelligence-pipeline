# Operations Runbook: Contract Intelligence Pipeline

This runbook covers critical operational scenarios for managing the Contract Intelligence Pipeline in staging and production environments.

---

## 1. System Monitoring: Health Indicators

To ensure the ingestion and multi-agent pipeline is operating correctly, monitor the following metrics and files:

### What to Watch
1. **Pipeline Execution Exit Codes**:
   - `0`: Normal completion.
   - `1`: Unexpected execution error (file access issues, invalid inputs).
   - `2`: Guardrail violation (e.g., Token/Cost Budget Limit exceeded).
2. **Schema Drift Directory**:
   - Path: `phase1_ingestion/output/`
   - Normal state: No new `drift_report_*.json` files.
   - Actionable state: Presence of a drift report means the source API has changed. This triggers an automated alert to the data engineering team, though ingestion continues without failure.
3. **Audit and Corrections Ledger**:
   - Path: `phase2_agents/output/audit_log.json` and `phase2_agents/output/corrections_pending_approval.json`
   - Normal state: Records accumulate. Average confidence levels for classifier and risk reviews should remain above `0.85`.
   - Concern state: Average confidence dropping below `0.75` indicates model drift or highly ambiguous contract language, requiring prompt adjustments or few-shot additions.

### Monitoring Log Patterns
- **Normal Log signals**:
  - `[Schema] Established baseline schema...` (on initial runs)
  - `[Success] Ingestion complete...`
  - `Session Token Usage: X/Y` (within healthy thresholds)
- **Concerning Log signals**:
  - `[Warning] SCHEMA DRIFT DETECTED!` (requires updating downstream normalization mappings)
  - `[Guardrail Blocked] Out-of-scope content detected...` (flagged input routing to dead-letter queue)
  - `[Guardrail Triggered] Process halted: Token budget exceeded...` (budget exceeded, execution stopped)

---

## 2. Agent Failure Recovery Procedures

When an agent fails (e.g., API timeout, connection drop, or fatal runtime exception) mid-workflow, follow these recovery guidelines:

### Immediate Alerts
- Any exit code other than `0` should send a notification to the platform SRE team via standard logging hooks (e.g., Datadog, CloudWatch, or Slack webhook).

### handling In-Flight Data
- **Bronze Layer (Ingestion)**: 
  - Raw JSON files are parsed sequentially. If a crash occurs during ingestion, the `ingestion_state.json` file preserves a list of successfully landed `clause_id` records.
  - In-flight data is protected. Re-running the ingestion script automatically reads the state file, skips the already processed items, and ingests the remaining records.
- **Silver/Gold Layer (Orchestrator)**:
  - If `run_workflow.py` fails mid-way, the completed analyses are already logged to `audit_log.json` incrementally.
  - To resume, look at the last `clause_id` processed in the console output. You can re-run the orchestrator; it will overwrite existing entries in the audit logs (idempotency is guaranteed by unique `clause_id` keys).

### Manual Recovery Steps
1. **Locate the crash site**: Inspect the error traceback in the logs to isolate the failure (e.g., Gemini API quota limits, network timeout, or budget guardrail trigger).
2. **Resolve the cause**:
   - *If Gemini API is down*: Verify internet access or switch the workflow temporarily to mock mode using the `--mock` flag to process non-critical batches.
   - *If Token budget was exceeded*: Assess whether the batch size was intentionally large. If yes, adjust the threshold using the `--max-tokens <new_limit>` parameter.
3. **Execute clean re-run**: Start the workflow. The pipeline will process the remaining records and normalize outputs.

---

## 3. State Rollback Guidelines

If corrupted data or an incorrect logic update is pushed to production, follow these steps to restore the pipeline to a known-good state.

### Rolling Back Code
1. Revert the repository to the last stable git tag:
   ```bash
   git checkout tags/v1.0.0
   ```
2. Redeploy the python script files to the worker nodes.

### Rolling Back the Data Layer
Since our pipeline uses a flat-file medallion structure, rolling back data states is clean and auditable:

1. **Clear Bronze Output Cache**:
   - Locate the partition folder under `phase1_ingestion/output/bronze/year=YYYY/month=MM/day=DD/` corresponding to the corrupted ingestion window.
   - Delete the raw `clause_*.json` files generated during the failure interval.
2. **Reset Ingestion State**:
   - Open `phase1_ingestion/ingestion_state.json`.
   - Remove the entry corresponding to the corrupted file (or run the ingestion script with the `--reset-state` flag to wipe checkpoints and start fresh).
3. **Reset Audit Logs**:
   - Open `phase2_agents/output/audit_log.json` and remove the rows corresponding to the corrupted run timestamps.
4. **Re-Run Ingestion**:
   - Re-execute the clean ingestion script with the correct source files:
     ```bash
     python phase1_ingestion/ingest.py
     ```
   - Re-run the review workflow to regenerate corrected audit logs:
     ```bash
     python phase2_agents/run_workflow.py
     ```
