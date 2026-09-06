"""Write gates. Intelligences propose; this validates; domains persist.

Not a process engine — cross-domain processes live in pai.workflows.
Actions have no extra kernel policy yet: call domains.actions.service directly.
"""

from pai.kernel.evidence.vault_apply import process_candidates as accept_vault_candidates
from pai.kernel.evidence.candidate_eval import evaluate_candidates_batch

__all__ = [
    "accept_vault_candidates",
    "evaluate_candidates_batch",
]
