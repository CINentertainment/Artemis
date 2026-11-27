"""Compatible research mutex with priority for queued operator questions.

Priority changes the next acquisition only; running work is never interrupted.
Reservations are refreshed from the durable voice queue and expire after a crash.
"""
from contextlib import contextmanager
from threading import Lock,local
from time import monotonic

class PriorityLock:
    def __init__(self):
        self._lock=Lock();self._state=Lock();self._local=local();self._reservations={}
    def reserve(self,key,seconds=120):
        with self._state:self._reservations[str(key)]=monotonic()+seconds
    def pending(self):
        with self._state:
            stamp=monotonic();self._reservations={k:v for k,v in self._reservations.items()if v>stamp};return bool(self._reservations)
    def sync(self,keys):
        with self._state:self._reservations={str(k):monotonic()+120 for k in keys}
    def acquire(self,blocking=True,timeout=-1,*,priority=None):
        priority=priority or getattr(self._local,'priority','interactive')
        if priority=='background'and self.pending():return False
        if timeout==-1:return self._lock.acquire(blocking)
        return self._lock.acquire(blocking,timeout)
    def release(self):self._lock.release()
    def locked(self):return self._lock.locked()
    @contextmanager
    def background(self,lane=None,target=None):
        previous=getattr(self._local,'priority','interactive');previous_collection=getattr(self._local,'collection',{});self._local.priority='background';self._local.collection={'lane':lane,'target':str(target)if target is not None else None,'hints':{}}
        try:yield
        finally:self._local.priority=previous;self._local.collection=previous_collection
    def collection_context(self):return getattr(self._local,'collection',{})
    def set_collection_hints(self,value):
        context=dict(self.collection_context());context['hints']=value;self._local.collection=context
    def __enter__(self):
        if not self.acquire():raise RuntimeError('Background work deferred for a queued operator question.')
        return self
    def __exit__(self,*args):self.release()
research_lock=PriorityLock()
