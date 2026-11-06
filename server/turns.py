"""Bounded in-process model turns; SQLite remains the recovery authority."""
from concurrent.futures import ThreadPoolExecutor
from threading import BoundedSemaphore

from .storage import DomainError


class TurnRunner:
    def __init__(self, store):
        self.store = store
        self.slots = BoundedSemaphore(2)
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='battle-turn')

    def submit(self, mid, payload, key):
        with self.store.connection() as db:
            cached = self.store.cached(db, mid + ':advance', key, payload)
        if cached:
            return cached
        if not self.slots.acquire(blocking=False):
            raise DomainError('MODEL_BUSY', 503)
        try:
            result, before = self.store.start_turn(mid, payload, key)
            if before is None:
                self.slots.release()
                return result
            self.executor.submit(self.execute, mid, result['job_id'], before)
            return result
        except BaseException:
            self.slots.release()
            raise

    def execute(self, mid, jid, before):
        try:
            self.store.run_turn(mid, jid, before)
        except Exception:
            # run_turn persists failure; exception details never enter player responses.
            pass
        finally:
            self.slots.release()

    def close(self):
        self.executor.shutdown(wait=True)
