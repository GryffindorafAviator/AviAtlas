"""Keep searches from seeing an index and its document record at different versions.

Switching a document to new chunks touches the vector index, the keyword index,
and the record, which share no transaction. Searches hold the guard shared;
the switch holds it exclusively for its short duration (~0.1 s), so a search
waits instead of failing the consistency check. Single process only, like the
rest of the application.
"""
from contextlib import contextmanager
from threading import Condition


class IndexGuard:
    def __init__(self):
        self._condition = Condition()
        self._readers = 0
        self._writing = False
        self._waiting_writers = 0

    @contextmanager
    def read(self):
        with self._condition:
            # Writers go first so a stream of searches cannot postpone a switch indefinitely.
            while self._writing or self._waiting_writers:
                self._condition.wait()
            self._readers += 1
        try:
            yield
        finally:
            with self._condition:
                self._readers -= 1
                self._condition.notify_all()

    @contextmanager
    def write(self):
        with self._condition:
            self._waiting_writers += 1
            while self._writing or self._readers:
                self._condition.wait()
            self._waiting_writers -= 1
            self._writing = True
        try:
            yield
        finally:
            with self._condition:
                self._writing = False
                self._condition.notify_all()


index_guard = IndexGuard()
