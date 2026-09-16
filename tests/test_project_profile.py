import importlib.util
import unittest


class ProjectProfileContractTests(unittest.TestCase):
    def test_project_profile_module_exists(self):
        self.assertIsNotNone(
            importlib.util.find_spec("storyloop.project_profile"),
            "repo-local project profiling must be implemented",
        )


if __name__ == "__main__":
    unittest.main()
