"""Emit agent work for a host dispatcher; never launch child agents in stages."""
from .legacy import AgentInvocation, plan, lock_submissions
from .jobs import AgentJob, emit_job, emit_stage_jobs, load_job
from .collect import collect_job, collect_jobs
from .purpose import emit_purpose_verification, accept_purpose_verification
def emit_literature_jobs(*args, **kwargs):
    """Registry intake precedes a design card and never dispatches processes."""
    from .literature import emit_literature_jobs as emit
    return emit(*args, **kwargs)

__all__ = ["AgentInvocation", "plan", "lock_submissions", "AgentJob", "emit_job", "emit_stage_jobs", "load_job", "collect_job", "collect_jobs", "emit_purpose_verification", "accept_purpose_verification", "emit_literature_jobs"]
