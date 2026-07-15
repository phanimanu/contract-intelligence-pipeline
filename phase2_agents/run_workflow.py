#!/usr/bin/env python3
"""
Contract Intelligence Pipeline - Phase 2 Orchestration & Execution
Author: Senior AI Platform Engineer
"""

import os
import sys
import json
import argparse
from datetime import datetime

# Import core classes from agents.py
from agents import (
    BudgetTracker,
    ScopeValidator,
    MockLLMProvider,
    GeminiLLMProvider,
    ClauseClassifierAgent,
    RiskFlaggingAgent,
    AuditLearningAgent,
    GuardrailViolation,
    ScopeBoundaryViolation,
    BudgetExceededError
)

# Define directories
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE_DIR = os.path.dirname(SCRIPT_DIR)
BRONZE_DIR = os.path.join(WORKSPACE_DIR, "phase1_ingestion", "output", "bronze")
FALLBACK_FILE = os.path.join(WORKSPACE_DIR, "data", "clauses_ingested_fallback.json")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "output")
AUDIT_LOG_FILE = os.path.join(OUTPUT_DIR, "audit_log.json")
CORRECTIONS_FILE = os.path.join(OUTPUT_DIR, "corrections_pending_approval.json")


def scan_bronze_clauses():
    """Recursively scan Bronze partition directories for ingested raw clauses."""
    clauses = []
    if not os.path.exists(BRONZE_DIR):
        return clauses

    for root, _, files in os.walk(BRONZE_DIR):
        for file in files:
            if file.endswith(".json") and file.startswith("clause_"):
                file_path = os.path.join(root, file)
                try:
                    with open(file_path, "r") as f:
                        record = json.load(f)
                        # Extract the raw clause payload
                        if "raw_payload" in record:
                            clauses.append(record["raw_payload"])
                except Exception as e:
                    print(f"[Warning] Failed to read Bronze file '{file}': {e}")
    return clauses


def run_orchestrator(args):
    # 1. Initialize Budget & Guardrails
    budget = BudgetTracker(max_tokens=args.max_tokens)
    
    # 2. Select LLM Provider
    api_key = os.environ.get("GEMINI_API_KEY")
    if args.mock or not api_key:
        if not api_key and not args.mock:
            print("[Info] No GEMINI_API_KEY found in environment. Defaulting to Mock LLM Provider.")
        llm = MockLLMProvider(budget_tracker=budget)
    else:
        llm = GeminiLLMProvider(api_key=api_key, budget_tracker=budget)

    # 3. Instantiate Agents
    classifier_agent = ClauseClassifierAgent(llm)
    risk_agent = RiskFlaggingAgent(llm)
    audit_agent = AuditLearningAgent(AUDIT_LOG_FILE, CORRECTIONS_FILE)

    # 4. Gather Ingested Contract Clauses
    print("\n[Orchestrator] Scanning Bronze layer storage for ingested clauses...")
    clauses = scan_bronze_clauses()
    
    if not clauses:
        print(f"[Info] No Bronze records found in '{BRONZE_DIR}'. "
              f"Loading fallback pre-ingested file '{FALLBACK_FILE}'...")
        if not os.path.exists(FALLBACK_FILE):
            print(f"[Error] Fallback file not found at '{FALLBACK_FILE}'. Run Phase 1 first.")
            sys.exit(1)
        try:
            with open(FALLBACK_FILE, "r") as f:
                fallback_data = json.load(f)
                clauses = fallback_data.get("clauses", [])
        except Exception as e:
            print(f"[Error] Failed to load fallback file: {e}")
            sys.exit(1)

    total_records = len(clauses)
    print(f"[Orchestrator] Loaded {total_records} clauses for review.")

    processed_count = 0
    skipped_count = 0
    flagged_count = 0

    # 5. Core Processing Loop
    for idx, clause in enumerate(clauses):
        clause_id = clause.get("clause_id", f"UNKNOWN-{idx}")
        # Support normalized fields from schema unification
        clause_text = clause.get("clause_text")
        
        # Determine category field (handles schema drift v2.1 vs v2.3 or fallback name)
        orig_category = clause.get("clause_category") or clause.get("clause_type") or clause.get("category")

        print(f"\n" + "="*80)
        print(f"[{idx+1}/{total_records}] Processing Clause: {clause_id}")
        print(f"Original Text snippet: {clause_text[:120]}...")

        # --- GUARDRAIL 1: Scope Boundary Validator ---
        try:
            ScopeValidator.validate(clause_text)
        except ScopeBoundaryViolation as sb_err:
            print(f"[Guardrail Blocked] {sb_err}")
            # Log violation details to the audit log as an out-of-scope transaction
            audit_agent.audit_transaction(
                clause_id=clause_id,
                original_clause_payload=clause,
                classifier_out={"clause_category": "OUT_OF_SCOPE", "confidence": 0.0, "reasoning": str(sb_err)},
                risk_out={"risk_flagged": True, "risk_description": "Scope Boundary Violation", "suggested_alternative": "N/A", "confidence": 0.0},
                human_override={
                    "corrected_category": "OUT_OF_SCOPE",
                    "corrected_risk_flagged": True,
                    "override_notes": f"Scope boundary guardrail rejected record: {sb_err}",
                }
            )
            skipped_count += 1
            continue

        # --- AGENT 1: Classification ---
        classifier_out = classifier_agent.analyze(clause_id, clause_text)
        category = classifier_out["clause_category"]
        print(f"  > Classifier Category: '{category}' (Confidence: {classifier_out['confidence']})")

        # --- AGENT 2: Risk Analysis ---
        risk_out = risk_agent.analyze(clause_id, category, clause_text)
        risk_flagged = risk_out["risk_flagged"]
        if risk_flagged:
            flagged_count += 1
            print(f"  > Risk Flagged: [YES] (Confidence: {risk_out['confidence']})")
            print(f"    Risk: {risk_out['risk_description']}")
            print(f"    Suggested Alt: {risk_out['suggested_alternative']}")
        else:
            print(f"  > Risk Flagged: [NO] (Confidence: {risk_out['confidence']})")

        # --- AGENT 3: Audit & HITL Boundary Validation ---
        human_override = None

        # HITL Criteria Check:
        # Require human review if:
        # A) Low model confidence (< 0.85) in either agent
        # B) Category is critical (indemnification, liability) AND risk is flagged
        needs_hitl = (
            classifier_out["confidence"] < 0.85 or
            risk_out["confidence"] < 0.85 or
            (category in ["indemnification", "limitation_of_liability", "liability"] and risk_flagged)
        )

        if needs_hitl:
            print(f"  > HITL Triggered: Critical risk context or low confidence.")
            
            # Interactive Human-In-The-Loop
            if args.hitl:
                print("\n[Human Interaction Required]")
                print(f"Clause text: {clause_text}")
                print(f"Model Classification: {category}")
                print(f"Model Risk Status: {'RISK FLAGGED' if risk_flagged else 'APPROVED'}")
                if risk_flagged:
                    print(f"Model Risk Explanation: {risk_out['risk_description']}")
                    print(f"Model Proposed Alternative: {risk_out['suggested_alternative']}")
                
                approve = input("Do you approve the model decisions? (y/n, default 'y'): ").strip().lower() or 'y'
                if approve != 'y':
                    print("\n--- Enter Human Overrides ---")
                    corrected_cat = input(f"Corrected Category [Enter to keep '{category}']: ").strip() or category
                    corrected_risk_in = input(f"Flag as Risk? (y/n) [Enter to keep '{'y' if risk_flagged else 'n'}']: ").strip().lower()
                    
                    if corrected_risk_in == 'y':
                        corrected_risk = True
                    elif corrected_risk_in == 'n':
                        corrected_risk = False
                    else:
                        corrected_risk = risk_flagged

                    corrected_desc = risk_out["risk_description"]
                    corrected_alt = risk_out["suggested_alternative"]
                    if corrected_risk:
                        corrected_desc = input(f"Risk Description [Enter to keep current]: ").strip() or corrected_desc
                        corrected_alt = input(f"Suggested Alternative [Enter to keep current]: ").strip() or corrected_alt
                    
                    notes = input("Reason for correction (required): ").strip()
                    while not notes:
                        notes = input("Please specify a reason for this override: ").strip()

                    human_override = {
                        "corrected_category": corrected_cat,
                        "corrected_risk_flagged": corrected_risk,
                        "corrected_risk_description": corrected_desc if corrected_risk else "",
                        "corrected_alternative": corrected_alt if corrected_risk else "",
                        "override_notes": notes
                    }
                    print("[HITL] Override saved successfully.")
            
            # Automated HITL Simulation (if not interactive)
            else:
                # We simulate an override for a specific clause to showcase functional capability
                # For instance, let's override CLZ-2025-0003 (high coverage insurance clause)
                if clause_id == "CLZ-2025-0003":
                    human_override = {
                        "corrected_category": "insurance",
                        "corrected_risk_flagged": True,
                        "corrected_risk_description": "Simulated Human Reviewer: High $5M limit verified, but approved as client refused to negotiate.",
                        "corrected_alternative": "Keep original high limit clause as project waiver has been signed by Executive Sponsor.",
                        "override_notes": "Executive override: project value ($120M) justifies higher liability premium risk."
                    }
                    print(f"  > [Simulated HITL Override] Logged human review for {clause_id}.")
                elif clause_id == "CLZ-2025-0014":
                    human_override = {
                        "corrected_category": "payment_terms",
                        "corrected_risk_flagged": False,
                        "corrected_risk_description": "",
                        "corrected_alternative": "",
                        "override_notes": "Accepted retainage clause as it is a municipal client. Retainage is legally required in this state."
                    }
                    print(f"  > [Simulated HITL Override] Logged human review for {clause_id}.")

        # Log details to transaction ledger
        audit_agent.audit_transaction(
            clause_id=clause_id,
            original_clause_payload=clause,
            classifier_out=classifier_out,
            risk_out=risk_out,
            human_override=human_override
        )
        
        processed_count += 1

    # Print final execution summaries
    print("\n" + "="*80)
    print("[Success] Workflow execution finished.")
    print(f"  Successfully processed: {processed_count} clauses")
    print(f"  Flagged risks:           {flagged_count} clauses")
    print(f"  Skipped (out-of-scope):  {skipped_count} clauses")
    print(f"  Total Session Cost:      ${budget.total_cost_usd:.6f} USD")
    print(f"  Audit log updated at:    '{AUDIT_LOG_FILE}'")
    print(f"  Pending corrections at:  '{CORRECTIONS_FILE}'")
    print("="*80 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Multi-agent contract review orchestrator.")
    parser.add_argument("--mock", action="store_true", help="Force Mock LLM Provider instead of Gemini API.")
    parser.add_argument("--hitl", action="store_true", help="Run in interactive Human-in-the-Loop CLI mode.")
    parser.add_argument("--max-tokens", type=int, default=15000, help="Halt workflow when total token budget is reached.")
    args = parser.parse_args()

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    try:
        run_orchestrator(args)
    except BudgetExceededError as budget_err:
        print(f"\n[Guardrail Triggered] Process halted: {budget_err}")
        sys.exit(2)
    except Exception as e:
        print(f"\n[Error] Workflow execution failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
