# Claude Code Guardrails for MVP_v0 Upgrade

## Goal
Upgrade MVP_v0 based on the latest team decisions and use case schema updates, while keeping the system runnable and business-oriented.

## Non-negotiables
1. Do NOT rewrite the whole repo.
2. Preserve the existing runnable MVP_v0 baseline.
3. Prefer small, reviewable changes.
4. Keep deterministic policy/action logic intact.
5. LLM remains renderer/explainer only.
6. All recommendations must be:
   - specific
   - diagnostic
   - compelling
   - prioritized
   - tied to business outcomes
7. Add tests for any schema or logic changes.
8. If data is missing, represent causes as hypotheses + checks, not hard conclusions.
9. Do NOT add real-time integrations or unrelated scope.
10. Output should support merchant-facing decision cards, not just raw scores.