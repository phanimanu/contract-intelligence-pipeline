#!/usr/bin/env python3
"""
Contract Intelligence Pipeline - Phase 1 Ingestion Script
Author: Senior AI Platform Engineer
"""

import os
import sys
import json
import argparse
from datetime import datetime

# Define file paths relative to this script
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE_DIR = os.path.dirname(SCRIPT_DIR)
DEFAULT_INPUT_DIR = os.path.join(WORKSPACE_DIR, "data")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "output")
BRONZE_DIR = os.path.join(OUTPUT_DIR, "bronze")
STATE_FILE = os.path.join(SCRIPT_DIR, "ingestion_state.json")
BASELINE_SCHEMA_FILE = os.path.join(SCRIPT_DIR, "schema_baseline.json")


def setup_directories():
    """Ensure output directories exist."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(BRONZE_DIR, exist_ok=True)


def extract_schema(obj):
    """
    Recursively extract schema structure (keys and value types) from a Python object.
    Represent types as strings.
    """
    if isinstance(obj, dict):
        return {k: extract_schema(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        if not obj:
            return ["list", "empty"]
        # Extract schema of the first element to represent list items type
        return ["list", extract_schema(obj[0])]
    elif obj is None:
        return "NoneType"
    else:
        return type(obj).__name__


def compare_schemas(baseline, current, path=""):
    """
    Recursively compare baseline schema with current schema.
    Returns lists of added, removed, and type_changed fields.
    """
    added = []
    removed = []
    type_changed = []

    if isinstance(baseline, dict) and isinstance(current, dict):
        # Check for added fields
        for key in current:
            current_path = f"{path}.{key}" if path else key
            if key not in baseline:
                added.append(current_path)
            else:
                add, rem, tc = compare_schemas(baseline[key], current[key], current_path)
                added.extend(add)
                removed.extend(rem)
                type_changed.extend(tc)

        # Check for removed fields
        for key in baseline:
            current_path = f"{path}.{key}" if path else key
            if key not in current:
                removed.append(current_path)

    elif isinstance(baseline, list) and isinstance(current, list):
        if len(baseline) > 1 and len(current) > 1:
            add, rem, tc = compare_schemas(baseline[1], current[1], f"{path}[]")
            added.extend(add)
            removed.extend(rem)
            type_changed.extend(tc)
    else:
        # Compare types
        if baseline != current:
            # Handle nullable type compatibility or literal type mismatch
            if baseline != "NoneType" and current != "NoneType":
                type_changed.append({
                    "path": path,
                    "expected": baseline,
                    "actual": current
                })

    return added, removed, type_changed


def load_state():
    """Load the ingestion resumability state."""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            print(f"[Warning] Failed to load state file: {e}. Starting fresh.")
    return {}


def save_state(state):
    """Save the ingestion resumability state."""
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        print(f"[Error] Failed to save state file: {e}")


def load_baseline_schema():
    """Load the schema baseline if it exists."""
    if os.path.exists(BASELINE_SCHEMA_FILE):
        try:
            with open(BASELINE_SCHEMA_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            print(f"[Warning] Failed to load schema baseline: {e}.")
    return None


def save_baseline_schema(schema):
    """Save the baseline schema."""
    try:
        with open(BASELINE_SCHEMA_FILE, "w") as f:
            json.dump(schema, f, indent=2)
        print(f"[Schema] Established baseline schema in '{BASELINE_SCHEMA_FILE}'")
    except Exception as e:
        print(f"[Error] Failed to save baseline schema: {e}")


def ingest_file(file_path, fail_after=None, force_schema_drift=False):
    """
    Ingest clauses from a JSON file into the Bronze layer, tracking progress.
    """
    print(f"\n[Ingest] Starting ingestion of '{file_path}'")
    
    if not os.path.exists(file_path):
        print(f"[Error] File not found: {file_path}")
        sys.exit(1)

    try:
        with open(file_path, "r") as f:
            data = json.load(f)
    except Exception as e:
        print(f"[Error] Failed to parse JSON file {file_path}: {e}")
        sys.exit(1)

    # 1. Extract and check Schema Drift
    current_schema = extract_schema(data)
    baseline_schema = load_baseline_schema()
    
    drift_detected = False
    drift_report = {}

    if baseline_schema is None:
        # Establish baseline using first ingested file
        save_baseline_schema(current_schema)
    else:
        # Compare schemas
        added, removed, type_changed = compare_schemas(baseline_schema, current_schema)
        
        # Analyze renames specifically for the clauses layer
        renamed = []
        # Check if clauses[].clause_type was removed and clauses[].category was added
        if "clauses[].clause_type" in removed and "clauses[].category" in added:
            renamed.append({
                "from": "clauses[].clause_type",
                "to": "clauses[].category"
            })
            removed.remove("clauses[].clause_type")
            added.remove("clauses[].category")

        if added or removed or type_changed or renamed:
            drift_detected = True
            drift_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            drift_report = {
                "timestamp": datetime.now().isoformat(),
                "file_processed": os.path.basename(file_path),
                "api_version_baseline": baseline_schema.get("metadata", {}).get("api_version", "unknown"),
                "api_version_current": data.get("metadata", {}).get("api_version", "unknown"),
                "drift_summary": {
                    "added_fields": added,
                    "removed_fields": removed,
                    "type_changed_fields": type_changed,
                    "renamed_fields": renamed
                }
            }
            
            report_filename = f"drift_report_{drift_timestamp}.json"
            report_path = os.path.join(OUTPUT_DIR, report_filename)
            with open(report_path, "w") as rf:
                json.dump(drift_report, rf, indent=2)
            
            print(f"[Warning] SCHEMA DRIFT DETECTED!")
            print(f"  Added fields: {added}")
            print(f"  Removed fields: {removed}")
            print(f"  Type changes: {type_changed}")
            print(f"  Renamed fields: {renamed}")
            print(f"  Drift report saved to '{report_path}'")

    # 2. Resumability Check
    state = load_state()
    file_key = os.path.basename(file_path)
    
    if file_key not in state:
        state[file_key] = {
            "file_path": file_path,
            "status": "in_progress",
            "processed_clause_ids": [],
            "last_updated": datetime.now().isoformat()
        }
    
    file_state = state[file_key]
    
    if file_state["status"] == "completed":
        print(f"[Info] File '{file_key}' has already been fully processed. Skipping.")
        return

    clauses = data.get("clauses", [])
    total_clauses = len(clauses)
    processed_in_this_run = 0
    skipped_count = 0

    # Land raw batch metadata for traceability
    ingestion_time = datetime.now()
    partition_path = os.path.join(
        BRONZE_DIR,
        f"year={ingestion_time.year:04d}",
        f"month={ingestion_time.month:02d}",
        f"day={ingestion_time.day:02d}"
    )
    os.makedirs(partition_path, exist_ok=True)

    print(f"[Ingest] Found {total_clauses} clauses in batch. Resuming state...")

    # We process clauses item by item to simulate real ingestion flow
    for idx, clause in enumerate(clauses):
        clause_id = clause.get("clause_id")
        if not clause_id:
            print(f"[Warning] Clause at index {idx} has no clause_id. Skipping landing.")
            continue

        # Check if already processed
        if clause_id in file_state["processed_clause_ids"]:
            skipped_count += 1
            continue

        # Simulate Fail-after for testing resumability
        if fail_after is not None and processed_in_this_run >= fail_after:
            print(f"[Simulated Failure] Intentionally halting ingestion after {fail_after} records.")
            save_state(state)
            raise RuntimeError(f"Simulated failure triggered after processing {fail_after} clauses.")

        # Ingest clause: land raw data to Bronze
        timestamp_str = ingestion_time.strftime("%Y%m%d_%H%M%S")
        bronze_filename = f"clause_{clause_id}_{timestamp_str}.json"
        bronze_filepath = os.path.join(partition_path, bronze_filename)

        # Raw Landing: preserve as-is with ingestion metadata
        bronze_payload = {
            "ingested_at": ingestion_time.isoformat(),
            "source_file": file_key,
            "raw_payload": clause
        }

        with open(bronze_filepath, "w") as bf:
            json.dump(bronze_payload, bf, indent=2)

        # Update state checkpoint
        file_state["processed_clause_ids"].append(clause_id)
        file_state["last_updated"] = datetime.now().isoformat()
        processed_in_this_run += 1
        
        # Save state progressively or in small batches
        if processed_in_this_run % 5 == 0 or processed_in_this_run == 1:
            save_state(state)

    # Clean finalization
    file_state["status"] = "completed"
    file_state["last_updated"] = datetime.now().isoformat()
    save_state(state)
    
    print(f"[Success] Ingestion complete for '{file_key}'.")
    print(f"  Processed {processed_in_this_run} new clauses.")
    print(f"  Skipped {skipped_count} already processed clauses.")
    print(f"  Bronze files written to '{partition_path}'")


def main():
    parser = argparse.ArgumentParser(description="Ingest contract clauses into Bronze layer.")
    parser.add_argument("--file", help="Path to input json file. If empty, runs default pipeline.")
    parser.add_argument("--fail-after", type=int, help="Simulate a failure after N records.")
    parser.add_argument("--reset-state", action="store_true", help="Clear checkpoints and baseline schemas.")
    args = parser.parse_args()

    setup_directories()

    if args.reset_state:
        if os.path.exists(STATE_FILE):
            os.remove(STATE_FILE)
            print("[Reset] Cleared state file.")
        if os.path.exists(BASELINE_SCHEMA_FILE):
            os.remove(BASELINE_SCHEMA_FILE)
            print("[Reset] Cleared schema baseline.")
        # Clean bronze folder contents to allow clean re-runs
        import shutil
        if os.path.exists(BRONZE_DIR):
            shutil.rmtree(BRONZE_DIR)
            os.makedirs(BRONZE_DIR)
            print("[Reset] Cleared Bronze storage.")
        return

    # If no file is specified, ingest default batch 1, then batch 2
    if not args.file:
        batch1 = os.path.join(DEFAULT_INPUT_DIR, "clauses_batch_1.json")
        batch2 = os.path.join(DEFAULT_INPUT_DIR, "clauses_batch_2.json")
        
        print("[Pipeline] Running default ingestion pipeline (Batch 1 followed by Batch 2)...")
        try:
            ingest_file(batch1, fail_after=args.fail_after)
            ingest_file(batch2, fail_after=args.fail_after)
        except Exception as e:
            print(f"[Pipeline Failed] {e}")
            sys.exit(1)
    else:
        try:
            ingest_file(args.file, fail_after=args.fail_after)
        except Exception as e:
            print(f"[Failed] {e}")
            sys.exit(1)


if __name__ == "__main__":
    main()
