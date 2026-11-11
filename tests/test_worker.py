"""Tests for predictive.worker."""

import threading
import time

from predictive.worker import PredictionWorker


class SlowPredictor:
    def __init__(self, gate=None):
        self.thread = threading.current_thread()
        self.calls = []
        self.gate = gate
        self.closed = False

    def predict(self, context, prefix):
        if self.gate is not None:
            self.gate.wait(2)
        self.calls.append((tuple(context), prefix))
        return [prefix + "x"]

    def close(self):
        self.closed = True


def collect():
    results = []
    done = threading.Event()

    def on_result(token, words):
        results.append((token, words, threading.current_thread()))
        done.set()

    return results, done, on_result


def test_result_comes_from_worker_thread():
    results, done, on_result = collect()
    holder = {}

    def factory():
        holder["p"] = SlowPredictor()
        return holder["p"]

    worker = PredictionWorker(factory, on_result)
    worker.submit(1, ["a"], "pre")
    assert done.wait(2)
    token, words, thread = results[0]
    assert token == 1 and words == ["prex"]
    assert thread is not threading.current_thread()
    assert holder["p"].thread is thread  # created in the worker thread too
    worker.close()
    assert holder["p"].closed


def test_only_latest_pending_request_runs():
    gate = threading.Event()
    results, done, on_result = collect()
    predictor = SlowPredictor(gate)
    worker = PredictionWorker(lambda: predictor, on_result)
    worker.submit(1, [], "a")  # starts, blocked on the gate
    time.sleep(0.1)
    worker.submit(2, [], "b")  # replaced by the next one before it starts
    worker.submit(3, [], "c")
    gate.set()
    deadline = time.time() + 2
    while len(results) < 2 and time.time() < deadline:
        time.sleep(0.01)
    assert [r[0] for r in results] == [1, 3]
    worker.close()


def test_factory_failure_gives_empty_results():
    results, done, on_result = collect()

    def factory():
        raise FileNotFoundError("model")

    worker = PredictionWorker(factory, on_result)
    worker.submit(7, [], "x")
    assert done.wait(2)
    assert results[0][:2] == (7, []) and worker.failed
    worker.close()
