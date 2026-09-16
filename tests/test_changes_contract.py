import importlib.util
import unittest


class ChangeContractTests(unittest.TestCase):
    def test_changes_module_exists(self):
        self.assertIsNotNone(
            importlib.util.find_spec("storyloop.changes"),
            "OpenSpec product change contracts must be implemented",
        )


if __name__ == "__main__":
    unittest.main()
