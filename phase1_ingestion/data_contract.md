# Data Contract: Contract Intelligence Ingestion Pipeline

**Version**: 1.0.0  
**Owner**: Senior AI Platform Engineer  
**Status**: Active  

---

## 1. Overview
This data contract defines the schema, types, constraints, and validation rules for contract clause data ingested from the external Contract Management API into the Bronze layer of the Medallion Data Platform.

---

## 2. Ingestion Guarantees
- **Raw Fidelity**: All incoming payloads are landed in the Bronze layer *as-is* without any structural transformation.
- **Partitioning**: Landed files are partitioned by ingestion timestamp using Hive-style partitions: `year=YYYY/month=MM/day=DD/`.
- **Traceability**: Every raw landed record is enclosed in an envelope containing ingestion metadata:
  - `ingested_at`: ISO-8601 timestamp of ingestion execution.
  - `source_file`: The filename of the batch payload source.
  - `raw_payload`: The original JSON clause object.

---

## 3. Schema Specifications

Downstream Silver-layer processes can rely on the following specifications based on the baseline schema (v2.1 / Batch 1).

### Metadata Schema
| Field Name | Type | Description | Required | Constraints |
|---|---|---|---|---|
| `source` | `str` | The source API system name | Yes | Must be `"contract_management_api"` |
| `api_version` | `str` | Version of the source API | Yes | e.g. `"2.1"`, `"2.3"` |
| `exported_at` | `str` | Time the API batch was exported | Yes | ISO-8601 format |
| `page` | `int` | Current batch page number | Yes | `>= 1` |
| `total_pages` | `int` | Total pages in the export | Yes | `>= 1` |
| `record_count` | `int` | Number of clauses in this batch | Yes | Matches `len(clauses)` |

### Clause Schema
| Field Name | Type | Description | Required | Constraints |
|---|---|---|---|---|
| `clause_id` | `str` | Unique clause identifier | Yes | Format `CLZ-YYYY-NNNN` |
| `contract_id` | `str` | Parent contract identifier | Yes | Format `CTR-NNNN` |
| `client_name` | `str` | Name of the client organization | Yes | Non-empty |
| `project_name` | `str` | Name of the architectural/engineering project | Yes | Non-empty |
| `clause_type` | `str` | Category identifier (v2.1 baseline) | Yes | See allowed values below |
| `clause_text` | `str` | Full text of the contract clause | Yes | Minimum 10 characters |
| `section_ref` | `str` | Document section reference | Yes | e.g., `"Section 8.1"` |
| `effective_date` | `str` | Date clause becomes active | Yes | ISO-8601 Date (`YYYY-MM-DD`) |
| `expiration_date` | `str` | Date clause expires | Yes | ISO-8601 Date (`YYYY-MM-DD`) |
| `status` | `str` | Review status of the clause | Yes | `active`, `draft`, `under_review` |
| `last_modified` | `str` | ISO-8601 timestamp of last modification | Yes | ISO-8601 |
| `modified_by` | `str` | Team/user that last updated the record | Yes (v2.1) | Removed in v2.3 |
| `review_history` | `dict` | Nested object tracking human reviews | No (v2.3) | Nullable |

### Allowed Clause Types (Baseline)
- `indemnification`
- `limitation_of_liability`
- `insurance`
- `payment_terms`
- `termination`
- `scope_of_work`
- `consequential_damages`
- `security_clearance`
- `other`

---

## 4. Schema Drift Management Policy

When schema modifications occur on the source API, the pipeline adheres to the following rules:

1. **Non-Breaking Ingestion**: Schema changes *must not* cause the ingestion script to crash or fail. Raw payloads must still be landed successfully in the Bronze layer.
2. **Detection & Reporting**:
   - The ingestion script compares the incoming JSON schema against the baseline schema stored in `schema_baseline.json`.
   - If keys are added, removed, type-changed, or renamed, a schema drift report is automatically written to `phase1_ingestion/output/drift_report_<timestamp>.json` and a warning is logged.
3. **Known Drifts (v2.1 to v2.3 Transition)**:
   - **Rename**: `clause_type` (v2.1) is renamed to `category` (v2.3). Downstream components (Silver layer) must normalize both to `clause_category`.
   - **Column Removal**: `modified_by` is removed in v2.3. Silver layer processes must allow nulls or provide a default value.
   - **New Nested Structure**: `review_history` object appears in v2.3, detailing a list of review objects. Downstream agents must parse this structure when present.
