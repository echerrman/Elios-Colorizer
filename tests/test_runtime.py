from concurrent.futures import ThreadPoolExecutor
import threading

from elios_colorizer.runtime import (EagerPrefetch, ResourcePreferences, StageTimings,
                                     plan_resources, prefetch, round_robin)


def test_high_core_resource_plan_scales_and_bounds_concurrency():
    gib = 1024**3
    plan = plan_resources(100_000_000, available_memory_bytes=64 * gib,
                          logical_cpus=64, flight_count=8)
    assert plan.worker_threads == 32
    assert plan.concurrent_flights == 8
    assert plan.workers_per_flight == 4
    cuda = plan_resources(100_000_000, available_memory_bytes=64 * gib,
                          logical_cpus=64, cuda_available=True, cuda_devices=1,
                          flight_count=8, fusion=True)
    assert cuda.concurrent_flights == 2
    assert cuda.cuda_streams == 1
    multi_cuda = plan_resources(100_000_000, available_memory_bytes=128 * gib,
                                logical_cpus=64, cuda_available=True, cuda_devices=4,
                                flight_count=12)
    assert multi_cuda.concurrent_flights == 4
    assert multi_cuda.workers_per_flight == 8


def test_custom_resource_plan_enforces_user_budgets_without_changing_default():
    gib=1024**3
    default=plan_resources(100_000_000,available_memory_bytes=64*gib,logical_cpus=64,
                           cuda_available=True,cuda_devices=4,flight_count=8)
    assert default.worker_threads==32 and default.concurrent_flights==4
    custom=ResourcePreferences(False,50,40,False,1,60,3,'low')
    limited=plan_resources(100_000_000,available_memory_bytes=64*gib,logical_cpus=64,
                           cuda_available=True,cuda_devices=4,flight_count=8,preferences=custom)
    assert limited.worker_threads==32
    assert limited.available_memory_bytes==int(64*gib*.4)
    assert not limited.cuda_available and limited.cuda_devices==0
    assert limited.concurrent_flights==3
    assert limited.workers_per_flight==10


def test_prefetch_is_ordered_and_uses_background_thread():
    threads = []
    def source():
        for value in range(5):
            threads.append(threading.current_thread().name)
            yield value
    assert list(prefetch(source(), 2)) == list(range(5))
    assert any(name.startswith('elios-prefetch') for name in threads)


def test_round_robin_and_timings():
    assert list(round_robin(([1, 2, 3], [10, 20]))) == [1, 10, 2, 20, 3]
    timings = StageTimings()
    with timings.measure('work'):
        pass
    assert timings.to_dict()['operations'] == {'work': 1}


def test_eager_prefetch_starts_before_consumption():
    produced = threading.Event()
    def source():
        produced.set()
        yield 1
    iterator = EagerPrefetch(source(), 1)
    assert produced.wait(1)
    assert list(iterator) == [1]
