from __future__ import annotations

import unittest

from scripts.benchmark_controller import (
    BENCHMARK_VERSION,
    _summarize_durations,
    run_benchmark,
)


class ControllerBenchmarkTests(unittest.TestCase):
    def test_duration_summary_reports_percentiles_and_budget(self) -> None:
        summary = _summarize_durations(
            [0.010, 0.020, 0.030, 0.040],
            budget_ms=35.0,
        )

        self.assertEqual(summary["iterations"], 4)
        self.assertEqual(summary["meanMs"], 25.0)
        self.assertEqual(summary["p95Ms"], 40.0)
        self.assertEqual(summary["maxMs"], 40.0)
        self.assertFalse(summary["passed"])

    def test_synthetic_benchmark_covers_controller_critical_paths(self) -> None:
        report = run_benchmark(
            iterations=1,
            warmup=0,
            budget_scale=1000.0,
        )

        self.assertEqual(report["benchmarkVersion"], BENCHMARK_VERSION)
        self.assertEqual(report["kind"], "SYNTHETIC_CONTROLLER_BENCHMARK")
        self.assertTrue(report["passed"])
        self.assertEqual(
            set(report["scenarios"]),
            {
                "entryRouter",
                "prepareAndFreeze",
                "workspaceStatus",
                "graphFrontier",
            },
        )
        for scenario in report["scenarios"].values():
            self.assertEqual(scenario["iterations"], 1)
            self.assertGreaterEqual(scenario["meanMs"], 0.0)
            self.assertGreater(scenario["budgetMs"], 0.0)

    def test_synthetic_benchmark_rejects_invalid_sample_counts(self) -> None:
        for iterations, warmup in ((0, 0), (1, -1)):
            with self.subTest(iterations=iterations, warmup=warmup):
                with self.assertRaises(ValueError):
                    run_benchmark(
                        iterations=iterations,
                        warmup=warmup,
                    )


if __name__ == "__main__":
    unittest.main()
