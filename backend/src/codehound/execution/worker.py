"""Dedicated execution worker. Run separately from the HTTP API."""

import argparse
import asyncio
import logging
import signal
from uuid import uuid4

from dotenv import load_dotenv
from starlette.concurrency import run_in_threadpool

from codehound.db.database import Database
from codehound.db.jobs import JobStore
from codehound.db.store import StoreConflict, VerificationStore
from codehound.evaluation.registry import EvaluationProfile
from codehound.execution.docker import control
from codehound.execution.independent import IndependentRunner
from codehound.execution.verify import verify_snapshot
from codehound.repositories.checkout import CheckoutFailure

logger = logging.getLogger(__name__)


class WorkerInterrupted(Exception):
    pass


async def execute_job(job, database, executor=verify_snapshot):
    profile = EvaluationProfile.model_validate(job.profile_snapshot)
    record = await run_in_threadpool(
        VerificationStore(database).get, job.verification_id, job.owner_id
    )
    snapshot = record.snapshot if record else None
    if not snapshot or (snapshot["base_sha"], snapshot["head_sha"], snapshot["diff_sha256"]) != (
        job.base_sha,
        job.head_sha,
        job.diff_sha256,
    ):
        raise ValueError("Pinned evidence changed.")
    if profile.repository.casefold() != snapshot["repository"]["full_name"].casefold():
        raise ValueError("Profile does not match the repository.")
    code, _, _ = await control("image", "inspect", job.image_id)
    if code:
        raise RuntimeError("Trusted image is not available locally.")
    suites = {"visible": profile.visible}
    if profile.hidden:
        suites["hidden"] = profile.hidden
    store = JobStore(database)

    async def progress(stage):
        await run_in_threadpool(store.progress, job.id, job.claim_token, stage)

    async with asyncio.timeout(600):
        artifact = await executor(
            snapshot,
            suites,
            IndependentRunner(job.image_id),
            mode="independent",
            on_progress=progress,
        )
    artifact["profile"] = profile.public()
    return artifact


async def process_job(job, database, stop, *, execute=execute_job, heartbeat_seconds=5):
    store = JobStore(database)

    async def guard():
        while True:
            if stop.is_set():
                raise WorkerInterrupted("Worker is stopping.")
            if await run_in_threadpool(store.renew, job.id, job.claim_token):
                raise WorkerInterrupted("Cancellation requested.")
            try:
                await asyncio.wait_for(stop.wait(), timeout=heartbeat_seconds)
            except TimeoutError:
                pass

    work = asyncio.create_task(execute(job, database))
    watcher = asyncio.create_task(guard())
    artifact, failure = None, None
    interrupted = False
    try:
        done, _ = await asyncio.wait([work, watcher], return_when=asyncio.FIRST_COMPLETED)
        if watcher in done:
            await watcher
        artifact = await work
    except asyncio.CancelledError:
        interrupted = True
        failure = {"code": "worker_stopped", "message": "The worker stopped during execution."}
    except WorkerInterrupted:
        failure = {
            "code": "execution_interrupted",
            "message": "Execution was cancelled or the worker stopped.",
        }
    except StoreConflict:
        failure = {"code": "lease_lost", "message": "The execution lease expired."}
    except CheckoutFailure:
        failure = {"code": "checkout_failed", "message": "Pinned revisions could not be prepared."}
    except TimeoutError:
        failure = {"code": "execution_timeout", "message": "Execution exceeded its deadline."}
    except Exception:
        logger.error("Execution failed for job %s", job.id)
        failure = {
            "code": "execution_failed",
            "message": "Evaluation failed. Check worker configuration and the trusted image.",
        }
    finally:
        for task in (work, watcher):
            if not task.done():
                task.cancel()
        await asyncio.gather(work, watcher, return_exceptions=True)
    try:
        await run_in_threadpool(
            store.finish, job.id, job.claim_token, artifact=artifact, failure=failure
        )
    except StoreConflict:
        pass  # A stale worker must never overwrite crash recovery or cancellation.
    if interrupted:
        raise asyncio.CancelledError


async def run_worker(database, stop, *, once=False):
    store = JobStore(database)
    worker_id = str(uuid4())

    async def heartbeat():
        while not stop.is_set():
            await run_in_threadpool(store.worker_heartbeat, worker_id)
            try:
                await asyncio.wait_for(stop.wait(), timeout=5)
            except TimeoutError:
                pass

    async def consume():
        while not stop.is_set():
            job = await run_in_threadpool(store.claim)
            if job:
                await process_job(job, database, stop)
            if once:
                return
            if not job:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=1)
                except TimeoutError:
                    pass

    pulse = asyncio.create_task(heartbeat())
    consumer = asyncio.create_task(consume())
    try:
        done, _ = await asyncio.wait([pulse, consumer], return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            await task
    finally:
        for task in (pulse, consumer):
            if not task.done():
                task.cancel()
        await asyncio.gather(pulse, consumer, return_exceptions=True)
        await run_in_threadpool(store.worker_stopped, worker_id)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.env_file:
        load_dotenv(args.env_file)
    database = Database()
    database.migrate()

    async def run():
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        await run_worker(database, stop, once=args.once)

    try:
        asyncio.run(run())
    finally:
        database.close()


if __name__ == "__main__":
    main()
