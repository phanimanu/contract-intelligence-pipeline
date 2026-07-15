# Buy vs. Build Rationale: Multi-Agent Orchestrator

For the multi-agent review workflow (Phase 2), we chose to **build a custom, lightweight orchestration layer** using raw Python, structured classes, and a direct provider pattern, rather than **buying/importing an existing multi-agent framework** (like CrewAI, AutoGen, or LangGraph).

---

## 1. Rationale for Building Custom Orchestration

1. **Minimized Dependency Footprint**:
   - Frameworks like CrewAI and AutoGen pull in dozens of transitive dependencies (e.g., Pydantic v1 vs v2 conflicts, heavy async libraries, telemetry scrapers). 
   - A lightweight custom implementation requires only the core LLM SDK and Pydantic, making it highly secure, stable, and quick to audit in enterprise environments.
2. **Deterministic Control Over Execution Flow**:
   - Legal review pipelines require strict sequential execution (first classify, then flag risks based on category, then audit).
   - Heavy frameworks rely on conversational loops or graph routing that can lead to non-deterministic loops, hallucinations, or runaway API costs. Building the control loop in raw Python guarantees that each clause is visited exactly once, in order.
3. **Seamless Guardrail Integration**:
   - Placing custom validation blocks (such as the `ScopeValidator` and character/cost `BudgetTracker`) directly between execution steps is trivial in a custom script, but requires complex middleware or custom tool wrappers in CrewAI/AutoGen.
4. **Predictable and Clean Mocking**:
   - Testing agent behavior offline with simulated responses is extremely simple when using a custom `MockLLMProvider` class. Mocking complex, agent-to-agent frameworks often requires mock servers or monkeypatching internal packages.

---

## 2. When to Revisit This Decision

We should revisit the choice of a custom orchestrator during the following milestones:
- **Scaling to >10 Agents**: If our contract review pipeline expands to include parallel sub-tasks (e.g., matching clauses against historical corporate litigation databases, parsing drawings, estimating costs, and checking local building codes concurrently), managing threads and communication states in raw Python will become complex.
- **Dynamic Task Planning Required**: If the pipeline needs to decide its own workflow dynamically (e.g., "If clause type is indemnification, spawn an insurance sub-agent AND a litigation-history agent; if payment, spawn a finance agent"), a graph-based framework would be cleaner.

---

## 3. Sunsetting Signals (When to Transition to LangGraph/CrewAI)

We will sunset the custom orchestrator and transition to a tool like LangGraph or CrewAI if we receive these operational signals:
- **Frequent State Machine Rewrites**: If our team spends more than 15% of development time writing state transition logic, retry policies, or async message passing code.
- **Requirement for Pre-built Agent Integrations**: If the business requires immediate integration with third-party tools that already have standard agent templates (e.g. connecting agents directly to Jira, Slack, Salesforce, and SharePoint out-of-the-box).
- **Need for Complex DAG Visualizations**: If business stakeholders require visual debugging tools (like LangSmith or LangGraph Studio) to inspect agent routing paths and state transitions in real time.
