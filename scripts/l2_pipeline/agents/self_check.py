"""Worker-side collector validation; no promotion, signature, or peer reads."""
import shlex
from pathlib import Path


def instruction(path):
    return ('\n<!-- C62_SELF_CHECK -->\nSonnet queue workers: use the token-bound sonnet_queue self-check --job-id ID --token TOKEN command instead of the direct command below; obey its one-correction limit.\nBefore submitting, run the SAME collector in validation-only mode from the repository root:\n'
        '.venv_did/bin/python -B -m scripts.l2_pipeline.agents.collect --self-check --job '+shlex.quote(str(path))+
        '\nCorrect your own submission until it passes, or report incomplete research truthfully. '
        'This command writes no promotions and grants no execution authority. Do not inspect collector inputs belonging to another role.\n<!-- /C62_SELF_CHECK -->\n')


def check(path):
    from .collect import collect_job
    value=collect_job(Path(path),self_check=True)
    if value.get('validated') is not True or value.get('mechanism_issues'):raise ValueError('Collector self-check rejected submission')
    return value


def receipt(path):
    """Same validator on every completed new worker, before transport submission.

    A rejection is retained for the scheduler's existing PARK/contract handling;
    it does not turn an incomplete answer into a transport failure or retry it.
    """
    from .jobs import load_job
    from ..storage import ensure_once,sha
    job=load_job(Path(path))
    try:value=check(path)
    except Exception as exc:value=dict(validated=False,self_check=True,error=type(exc).__name__+': '+str(exc),execution_authorized=False)
    value['submission_sha256']=sha(job.output_path)
    return ensure_once(Path(job.output_dir)/('SELF_CHECK_'+value['submission_sha256']+'.json'),value)
