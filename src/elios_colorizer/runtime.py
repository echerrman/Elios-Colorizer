"""Resource planning, bounded prefetch, and lightweight stage instrumentation."""
from __future__ import annotations

from collections import defaultdict
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict, dataclass
import os
import platform
import threading
import time
import queue
from typing import Callable, Iterable, Iterator, TypeVar


T = TypeVar("T")


@dataclass(frozen=True)
class ResourcePreferences:
    use_recommended: bool = True
    cpu_percent: int = 75
    memory_percent: int = 60
    cuda_enabled: bool = True
    max_gpus: int = 1
    vram_percent: int = 75
    concurrent_flights: int = 2
    process_priority: str = 'normal'

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, value) -> 'ResourcePreferences':
        if not isinstance(value, dict):
            return cls()
        result = cls(bool(value.get('use_recommended', True)),
                     max(10, min(100, int(value.get('cpu_percent', 75)))),
                     max(20, min(90, int(value.get('memory_percent', 60)))),
                     bool(value.get('cuda_enabled', True)),
                     max(1, int(value.get('max_gpus', 1))),
                     max(30, min(90, int(value.get('vram_percent', 75)))),
                     max(1, int(value.get('concurrent_flights', 2))),
                     str(value.get('process_priority', 'normal')).lower())
        return result if result.process_priority in ('low', 'normal', 'high') else cls(**{**result.to_dict(), 'process_priority': 'normal'})


@dataclass(frozen=True)
class HardwareInventory:
    cpu_name: str
    logical_cpus: int
    total_memory_bytes: int | None
    available_memory_bytes: int | None
    cuda_available: bool
    cuda_devices: int
    gpu_name: str | None
    gpu_total_memory_bytes: int | None
    gpu_free_memory_bytes: int | None
    cuda_summary: str


def memory_status() -> tuple[int | None, int | None]:
    try:
        if os.name == 'nt':
            import ctypes
            class MemoryStatus(ctypes.Structure):
                _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong),
                            ('total_physical', ctypes.c_ulonglong), ('available_physical', ctypes.c_ulonglong),
                            ('total_page_file', ctypes.c_ulonglong), ('available_page_file', ctypes.c_ulonglong),
                            ('total_virtual', ctypes.c_ulonglong), ('available_virtual', ctypes.c_ulonglong),
                            ('available_extended_virtual', ctypes.c_ulonglong)]
            status = MemoryStatus();status.length = ctypes.sizeof(status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.total_physical), int(status.available_physical)
        page_size=os.sysconf('SC_PAGE_SIZE');return int(page_size*os.sysconf('SC_PHYS_PAGES')),int(page_size*os.sysconf('SC_AVPHYS_PAGES'))
    except (AttributeError,OSError,ValueError):
        return None,None


def detect_hardware(*, refresh_cuda=False) -> HardwareInventory:
    from .cuda_backend import cuda_checkpoint, refresh_cuda_checkpoint
    if refresh_cuda:refresh_cuda_checkpoint()
    checkpoint=cuda_checkpoint();total,available=memory_status()
    cpu=(platform.processor() or os.environ.get('PROCESSOR_IDENTIFIER') or 'Unknown CPU').strip()
    return HardwareInventory(cpu,os.cpu_count() or 1,total,available,checkpoint.available,
                             checkpoint.device_count if checkpoint.available else 0,
                             checkpoint.device_name if checkpoint.available else None,
                             checkpoint.total_memory_bytes,checkpoint.free_memory_bytes,
                             checkpoint.summary)


@dataclass(frozen=True)
class ResourcePlan:
    logical_cpus: int
    available_memory_bytes: int | None
    cuda_available: bool
    cuda_devices: int
    worker_threads: int
    frame_batch_size: int
    prefetch_depth: int
    concurrent_flights: int
    workers_per_flight: int
    cuda_streams: int

    def to_dict(self) -> dict:
        return asdict(self)


def plan_resources(point_count: int, *, available_memory_bytes: int | None = None,
                   logical_cpus: int | None = None, cuda_available: bool = False,
                   cuda_devices: int = 0, flight_count: int = 1,
                   fusion: bool = False,
                   available_vram_bytes: int | None = None,
                   preferences: ResourcePreferences | dict | None = None) -> ResourcePlan:
    """Choose a bounded plan that scales beyond the old fixed 8/12 worker cap."""
    logical = max(1, logical_cpus if logical_cpus is not None else (os.cpu_count() or 1))
    prefs=(preferences if isinstance(preferences,ResourcePreferences) else ResourcePreferences.from_dict(preferences)) if preferences is not None else ResourcePreferences()
    custom=not prefs.use_recommended
    planned_memory=(int(available_memory_bytes*prefs.memory_percent/100) if custom and available_memory_bytes is not None else available_memory_bytes)
    memory_gib = planned_memory / 1024**3 if planned_memory is not None else 0
    if custom:
        cuda_available=bool(cuda_available and prefs.cuda_enabled)
        cuda_devices=min(cuda_devices,max(1,prefs.max_gpus)) if cuda_available else 0
    # NumPy/OpenCV release the GIL in the expensive projection operations. Leave
    # two logical CPUs for decoding, I/O, the UI, and the CUDA submission thread.
    worker_threads = (max(1,min(logical,round(logical*prefs.cpu_percent/100))) if custom else
                      max(1, min(32, logical - (2 if logical >= 6 else 1))))
    memory_cap = (32 if memory_gib >= 32 else 24 if memory_gib >= 20 else
                  16 if memory_gib >= 12 else 12 if memory_gib >= 8 else
                  8 if memory_gib >= 5 else 4)
    if custom and cuda_available and available_vram_bytes is not None:
        vram_gib=available_vram_bytes*prefs.vram_percent/100/1024**3
        vram_cap=(32 if vram_gib>=12 else 24 if vram_gib>=8 else 16 if vram_gib>=5 else 8 if vram_gib>=3 else 4)
        memory_cap=min(memory_cap,vram_cap)
    desired_batch = (32 if cuda_available and point_count >= 50_000_000 else
                     24 if cuda_available and point_count >= 10_000_000 else
                     24 if point_count >= 50_000_000 else
                     12 if point_count >= 10_000_000 else 8)
    frame_batch_size = min(memory_cap, desired_batch)
    # Independent CPU flights may overlap; one active projection job per CUDA
    # device avoids destructive contention while still permitting preparation.
    cpu_slots = max(1, logical // 8)
    memory_slots = max(1, int(memory_gib // (8 if fusion else 6))) if memory_gib else 1
    # With one GPU, permit one additional flight to prepare/decode while the
    # current flight owns the GPU. Projection itself is serialized by a gate.
    scheduling_slots = max(2, cuda_devices) if cuda_available else cpu_slots
    concurrent = (min(max(1,flight_count),max(1,prefs.concurrent_flights)) if custom else
                  min(max(1, flight_count), scheduling_slots, memory_slots))
    workers_per_flight = max(1, worker_threads // concurrent)
    return ResourcePlan(logical, planned_memory, cuda_available, cuda_devices,
                        worker_threads, frame_batch_size, 2, concurrent,
                        workers_per_flight, 1 if cuda_available else 0)


@contextmanager
def process_priority(level: str):
    """Temporarily apply a conservative Windows process priority class."""
    if os.name != 'nt' or level not in ('low','high'):
        yield;return
    import ctypes
    kernel=ctypes.windll.kernel32;handle=kernel.GetCurrentProcess();previous=kernel.GetPriorityClass(handle)
    target=0x00004000 if level=='low' else 0x00008000  # BELOW_NORMAL / ABOVE_NORMAL
    changed=bool(kernel.SetPriorityClass(handle,target))
    try:yield
    finally:
        if changed and previous:kernel.SetPriorityClass(handle,previous)


class StageTimings:
    """Thread-safe accumulated wall times and counters for processing reports."""
    def __init__(self) -> None:
        self._seconds: dict[str, float] = defaultdict(float)
        self._counts: dict[str, int] = defaultdict(int)
        self._lock = threading.Lock()

    @contextmanager
    def measure(self, stage: str):
        started = time.perf_counter()
        try:
            yield
        finally:
            with self._lock:
                self._seconds[stage] += time.perf_counter() - started
                self._counts[stage] += 1

    def to_dict(self) -> dict:
        with self._lock:
            return {
                "seconds": {key: round(value, 4) for key, value in sorted(self._seconds.items())},
                "operations": dict(sorted(self._counts.items())),
            }

    def add(self, stage: str, seconds: float, operations: int = 1) -> None:
        with self._lock:
            self._seconds[stage] += max(0., float(seconds))
            self._counts[stage] += max(0, int(operations))


def prefetch(iterable: Iterable[T], depth: int = 2,
             executor: ThreadPoolExecutor | None = None) -> Iterator[T]:
    """Read the next item on one background thread with strictly bounded memory."""
    if depth < 1:
        yield from iterable
        return
    source = iter(iterable)
    owned = executor is None
    pool = executor or ThreadPoolExecutor(max_workers=1, thread_name_prefix="elios-prefetch")
    futures: list[Future] = []

    def next_item():
        try:
            return True, next(source)
        except StopIteration:
            return False, None

    try:
        for _ in range(depth):
            futures.append(pool.submit(next_item))
        while futures:
            present, item = futures.pop(0).result()
            if not present:
                for future in futures:
                    future.cancel()
                return
            futures.append(pool.submit(next_item))
            yield item
    finally:
        if owned:
            pool.shutdown(wait=True, cancel_futures=True)


def round_robin(iterables: Iterable[Iterable[T]]) -> Iterator[T]:
    """Fairly interleave lazy per-flight streams without changing each stream's order."""
    active = [iter(item) for item in iterables]
    while active:
        remaining = []
        for stream in active:
            try:
                yield next(stream)
                remaining.append(stream)
            except StopIteration:
                pass
        active = remaining


class EagerPrefetch(Iterator[T]):
    """Immediately start a bounded producer, allowing preparation before a gate."""
    _END = object()

    def __init__(self, iterable: Iterable[T], depth: int = 2):
        self._source = iter(iterable)
        self._queue: queue.Queue = queue.Queue(maxsize=max(1, depth))
        self._closed = threading.Event()
        self._exhausted = False
        self._thread = threading.Thread(target=self._produce, name='elios-eager-prefetch', daemon=True)
        self._thread.start()

    def _put(self, value) -> bool:
        while not self._closed.is_set():
            try:
                self._queue.put(value, timeout=.1)
                return True
            except queue.Full:
                pass
        return False

    def _produce(self) -> None:
        try:
            for item in self._source:
                if not self._put((True, item)):
                    return
            self._put((False, self._END))
        except BaseException as exc:
            self._put((False, exc))

    def __iter__(self):
        return self

    def __next__(self) -> T:
        if self._exhausted:
            raise StopIteration
        valid, value = self._queue.get()
        if valid:
            return value
        self.close()
        if value is self._END:
            self._exhausted = True
            raise StopIteration
        self._exhausted = True
        raise value

    def close(self) -> None:
        self._closed.set()
        if threading.current_thread() is not self._thread:
            self._thread.join(timeout=1)
