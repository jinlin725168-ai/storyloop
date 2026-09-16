import unittest

import storyloop.baseline as baseline


class BaselineApiTests(unittest.TestCase):
    def test_exposes_bootstrap_and_load_operations(self):
        self.assertTrue(callable(getattr(baseline, "bootstrap_project", None)))
        self.assertTrue(callable(getattr(baseline, "load_baseline", None)))


if __name__ == "__main__":
    unittest.main()
