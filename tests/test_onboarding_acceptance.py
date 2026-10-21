"""Public reproducible source -> scaffold -> lock -> smoke -> readiness acceptance."""
from pathlib import Path
import shutil
import tempfile
import unittest

from tests.onboarding_acceptance import measure_sample


class PublicOnboardingAcceptanceTests(unittest.TestCase):
    def test_independent_python_repository_reaches_ready_with_real_behavior(self):
        with tempfile.TemporaryDirectory() as folder:
            result = measure_sample(Path(folder), mixed=False)
            self.assertEqual(result["readiness"], "READY")
            self.assertEqual(result["results"][0]["counts"]["executed"], 1)

    @unittest.skipUnless(all(shutil.which(name) for name in ("javac", "java", "node")),
                         "native JDK and Node required; no dependencies are installed")
    def test_maven_node_shape_runs_native_java_and_node_offline(self):
        with tempfile.TemporaryDirectory() as folder:
            result = measure_sample(Path(folder), mixed=True)
            self.assertEqual(result["readiness"], "READY")
            self.assertEqual(sum(item["counts"]["executed"] for item in result["results"]), 2)
            self.assertEqual(result["maven_plugin_build"], "NOT_RUN")
