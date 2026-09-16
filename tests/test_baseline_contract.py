import importlib.util
import unittest


class BaselineContractTests(unittest.TestCase):
    def test_baseline_module_exists(self):
        self.assertIsNotNone(
            importlib.util.find_spec("storyloop.baseline"),
            "single-repository baseline locking must be implemented",
        )


if __name__ == "__main__":
    unittest.main()
