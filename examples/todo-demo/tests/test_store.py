import tempfile
import unittest
from pathlib import Path

from app import store


class StoreTest(unittest.TestCase):
    def test_add_then_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "todo.json"
            store.add(path, "买牛奶")
            self.assertEqual([item["title"] for item in store.load(path)], ["买牛奶"])
