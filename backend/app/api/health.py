from fastapi import APIRouter

from app.jobs.pool import get_arq_pool

router = APIRouter(tags=["health"])


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.post("/health/job-test")
async def job_test(message: str = "hello from job-test"):
    """Enqueues the dummy job so Phase 0's DoD can be verified end to end:
    call this endpoint, then check the worker's logs for the message.
    """
    pool = await get_arq_pool()
    job = await pool.enqueue_job("dummy_job", message)
    return {"enqueued": True, "job_id": job.job_id if job else None}
