"""Private collection process sharing the persistent market queue with the UI."""
import signal
import threading
from pathlib import Path

from .market import Market
from .market_jobs import run_worker


def serve_worker(market, stop, workers=2):
    """Stop claiming new work on shutdown, then let in-flight captures settle."""
    if stop.is_set():
        return
    threads = run_worker(market, stop, workers=workers)
    scheduler = threading.Thread(
        target=market.scheduler, args=(stop,), name='market-scheduler', daemon=False
    )
    try:
        scheduler.start()
        print('Market collector ready; durable queue, bounded workers, no HTTP listener', flush=True)
        while not stop.wait(1):
            if not scheduler.is_alive() or any(not thread.is_alive() for thread in threads):
                raise RuntimeError('Market collector thread exited unexpectedly')
    finally:
        stop.set()
        if scheduler.ident is not None:
            scheduler.join()
        # Existing network requests have connector timeouts. A container forced
        # down after stop_grace_period recovers unfinished durable jobs on restart.
        for thread in threads:
            thread.join()


def main(workspace=Path('/workspace'), state=Path('/state')):
    stop = threading.Event()
    previous = {}

    def request_stop(signum, frame):
        stop.set()

    try:
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous[signum] = signal.getsignal(signum)
            signal.signal(signum, request_stop)
        serve_worker(Market(workspace, state), stop, workers=2)
    finally:
        stop.set()
        for signum, handler in previous.items():
            signal.signal(signum, handler)


if __name__ == '__main__':
    main()
