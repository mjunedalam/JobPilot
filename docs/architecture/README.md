# JobPilot Architecture

All diagrams are Mermaid and render on GitHub and in VS Code (Markdown Preview Mermaid Support).

| Doc | What it shows |
|-----|---------------|
| [01-project-architecture.md](01-project-architecture.md) | Layers, runtime processes, data stores, external systems |
| [agents/](agents/) | One page per agent: I/O schema, tools, internal flow, HITL points |
| [02-multi-agent-graph.md](02-multi-agent-graph.md) | The LangGraph supervisor graph and every interrupt |
| [03-holistic-view.md](03-holistic-view.md) | End-to-end journey and the application status lifecycle |
| [04-agent-harness.md](04-agent-harness.md) | Shared runtime: tools, guardrails, budgets, approvals, retries |
| [05-llm-gateway.md](05-llm-gateway.md) | Role-based routing across Anthropic and open-source models |
| [06-agentic-rag.md](06-agentic-rag.md) | Retrieval agent with grading, rewriting and abstention |
| [07-observability-and-evals.md](07-observability-and-evals.md) | Langfuse tracing and TruLens evaluation |
| [08-api.md](08-api.md) | REST API and OpenAPI spec |
| [09-tech-stack.md](09-tech-stack.md) | Technology choices and reasons |
| [10-web-ui.md](10-web-ui.md) | Web UI pages, data flow and design direction |

## Per-agent pages

1. [Orchestrator / Supervisor](agents/01-orchestrator.md)
2. [Portal Connector & Auth](agents/02-portal-connector.md)
3. [Profile](agents/03-profile.md)
4. [Job Search](agents/04-job-search.md)
5. [Matching & Ranking](agents/05-matching.md)
6. [Application](agents/06-application.md)
7. [Email Monitor](agents/07-email-monitor.md)
8. [Reply Drafting](agents/08-reply-drafting.md)
9. [Analytics & Reporting](agents/09-analytics.md)
