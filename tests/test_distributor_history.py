import json
import multiprocessing
import os
import tempfile
import threading
import time
import unittest
from unittest import mock

import app as app_module


def _save_history_in_process(
    history_file,
    deleted_file,
    name,
    write_started=None,
    allow_write=None,
    lock_attempted=None,
):
    app_module.DISTRIBUTOR_HISTORY_FILE = history_file
    app_module.DISTRIBUTOR_HISTORY_DELETED_FILE = deleted_file
    if lock_attempted is not None:
        original_flock = app_module.fcntl.flock

        def observed_flock(*args, **kwargs):
            lock_attempted.set()
            return original_flock(*args, **kwargs)

        app_module.fcntl.flock = observed_flock
    if write_started is not None and allow_write is not None:
        original_dump = app_module.json.dump

        def delayed_history_dump(*args, **kwargs):
            target = getattr(args[1], "name", "")
            if target.startswith(history_file + "."):
                write_started.set()
                allow_write.wait(timeout=5)
            return original_dump(*args, **kwargs)

        app_module.json.dump = delayed_history_dump
    app_module.save_distributor_history(name)


class DistributorHistoryPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.history_file = os.path.join(self.tempdir.name, "distributor_history.json")
        self.deleted_file = os.path.join(self.tempdir.name, "distributor_history_deleted.json")
        self.patches = [
            mock.patch.object(app_module, "DISTRIBUTOR_HISTORY_FILE", self.history_file),
            mock.patch.object(app_module, "DISTRIBUTOR_HISTORY_DELETED_FILE", self.deleted_file),
            mock.patch.object(app_module, "own_dealer_name", return_value="自有经销商"),
        ]
        for patcher in self.patches:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self.patches):
            patcher.stop()
        self.tempdir.cleanup()

    def test_empty_queried_history_does_not_rewrite_imported_history(self):
        app_module.import_distributor_history_many(["已导入分销商"])
        fixed_time_ns = 1_000_000_000
        os.utime(self.history_file, ns=(fixed_time_ns, fixed_time_ns))

        with mock.patch.object(app_module, "queried_dealer_history", return_value=[]):
            self.assertEqual(app_module.combined_distributor_history(), ["已导入分销商"])

        self.assertEqual(os.stat(self.history_file).st_mtime_ns, fixed_time_ns)

    def test_bulk_import_persists_every_distributor(self):
        distributors = [f"分销商-{index:03d}" for index in range(120)]

        app_module.import_distributor_history_many(distributors)

        self.assertEqual(app_module.load_distributor_history(), distributors)

    def test_read_during_write_never_observes_an_empty_history(self):
        app_module._save_distributor_history_rows(["原分销商"])
        original_dump = app_module.json.dump
        write_started = threading.Event()
        allow_write = threading.Event()
        read_result = []

        def slow_dump(*args, **kwargs):
            write_started.set()
            allow_write.wait(timeout=2)
            return original_dump(*args, **kwargs)

        with mock.patch.object(app_module.json, "dump", side_effect=slow_dump):
            writer = threading.Thread(
                target=app_module._save_distributor_history_rows,
                args=(["新分销商"],),
            )
            writer.start()
            self.assertTrue(write_started.wait(timeout=2))
            reader = threading.Thread(
                target=lambda: read_result.append(app_module.load_distributor_history())
            )
            reader.start()
            time.sleep(0.05)
            allow_write.set()
            writer.join(timeout=2)
            reader.join(timeout=2)

        self.assertFalse(writer.is_alive())
        self.assertFalse(reader.is_alive())
        self.assertIn(read_result[0], (["原分销商"], ["新分销商"]))

    def test_separate_process_writes_do_not_lose_a_distributor(self):
        app_module._save_distributor_history_rows(["原分销商"])
        context = multiprocessing.get_context("spawn")
        write_started = context.Event()
        allow_write = context.Event()
        second_lock_attempted = context.Event()
        first = context.Process(
            target=_save_history_in_process,
            args=(self.history_file, self.deleted_file, "分销商-A", write_started, allow_write),
        )

        second = context.Process(
            target=_save_history_in_process,
            args=(self.history_file, self.deleted_file, "分销商-B", None, None, second_lock_attempted),
        )
        first.start()
        self.assertTrue(write_started.wait(timeout=5))
        second.start()
        self.assertTrue(second_lock_attempted.wait(timeout=5))
        allow_write.set()
        first.join(timeout=5)
        second.join(timeout=5)

        self.assertEqual(first.exitcode, 0)
        self.assertEqual(second.exitcode, 0)
        self.assertEqual(
            set(app_module.load_distributor_history()),
            {"原分销商", "分销商-A", "分销商-B"},
        )


if __name__ == "__main__":
    unittest.main()
