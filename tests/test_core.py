"""Requirements for strict product contracts and the exclusive project lock."""
import tempfile
import unittest
from pathlib import Path
from tests.support import product
from storyloop.models import StoryLoopError
from storyloop.product_contract import validate_product
from storyloop.storage import locked


class ContractTests(unittest.TestCase):
    def test_rejects_duplicate_acceptance_ids(self):
        value = product()
        value["stories"][0]["acceptance"] *= 2
        with self.assertRaises(StoryLoopError):
            validate_product(value)

    def test_rejects_forward_dependencies_and_empty_business_outcome(self):
        value = product()
        value["stories"][0]["depends_on"] = ["ST-2"]
        with self.assertRaises(StoryLoopError):
            validate_product(value)
        value = product()
        value["stories"][0]["acceptance"][0]["then"] = " "
        with self.assertRaises(StoryLoopError):
            validate_product(value)


class LockTests(unittest.TestCase):
    def test_second_runner_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            with locked(project):
                with self.assertRaises(StoryLoopError):
                    with locked(project):
                        pass


if __name__ == "__main__":
    unittest.main()
