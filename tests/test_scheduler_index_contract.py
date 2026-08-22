from __future__ import annotations

from tempfile import TemporaryDirectory
import unittest

from hdg.repository import SchedulerRepository


class SchedulerIndexContractTests(unittest.TestCase):
    def test_event_pages_use_run_and_event_id_index(self) -> None:
        with TemporaryDirectory() as root:
            repository = SchedulerRepository(root)
            with repository.transaction() as connection:
                index_columns = [
                    row[2]
                    for row in connection.execute(
                        "PRAGMA index_info(graph_events_by_run_event_id)"
                    )
                ]

        self.assertEqual(index_columns, ["run_id", "event_id"])


if __name__ == "__main__":
    unittest.main()
