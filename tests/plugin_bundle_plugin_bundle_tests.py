from __future__ import annotations

from .plugin_bundle_support import unittest
from .plugin_bundle_routing import PluginBundleTestsPart1
from .plugin_bundle_runtime import PluginBundleTestsPart2


class PluginBundleTests(
    PluginBundleTestsPart1,
    PluginBundleTestsPart2,
    unittest.TestCase,
):
    pass
