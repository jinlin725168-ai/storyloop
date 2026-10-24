"""Execute the packaged survey's sampling recipe against independent vectors."""
import json
import re
import subprocess
import sys
import unittest

from storyloop.skills import SKILLS_ROOT


SEED = '0123456789abcdef0123456789abcdef'
DOCUMENTS = ['modules', 'architecture', 'extension', 'api', 'capabilities', 'building-blocks', 'testing']


class FoundationSamplingTests(unittest.TestCase):
    def sample(self, candidates, seed=SEED):
        reference = SKILLS_ROOT / 'foundation-survey/references/source-and-sampling.md'
        if not reference.is_file():
            self.fail('The packaged skill cannot supply an executable sampling recipe')
        blocks = re.findall(r'```python\n(.*?)\n```', reference.read_text(encoding='utf-8'), re.S)
        self.assertEqual(len(blocks), 1, 'One complete recipe must be executable without stitching snippets')
        namespace = {}
        exec(compile(blocks[0], str(reference), 'exec'), namespace)
        return namespace['sample_facts'](candidates, seed)

    def candidates(self, count, documents=DOCUMENTS):
        return [(f'openspec/foundation/{documents[i % len(documents)]}.md', f'F{i:03}')
                for i in range(count)]

    def test_each_body_is_represented_including_additional_markdown(self):
        for names in (DOCUMENTS, DOCUMENTS + ['extra-runtime']):
            with self.subTest(documents=len(names)):
                result = self.sample(self.candidates(len(names), names))
                self.assertEqual(result['N'], len(names))
                self.assertEqual(result['D'], len(names))
                self.assertEqual(result['sample_size'], len(names))
                self.assertEqual({row[0] for row in result['samples']},
                                 {f'openspec/foundation/{name}.md' for name in names})

    def test_hundred_facts_have_ten_replayable_samples_and_complete_digest(self):
        candidates = self.candidates(100)
        result = self.sample(candidates)
        expected = [
            ['openspec/foundation/api.md', 'F045'], ['openspec/foundation/api.md', 'F017'],
            ['openspec/foundation/architecture.md', 'F001'], ['openspec/foundation/architecture.md', 'F057'],
            ['openspec/foundation/building-blocks.md', 'F040'], ['openspec/foundation/testing.md', 'F069'],
            ['openspec/foundation/api.md', 'F024'], ['openspec/foundation/capabilities.md', 'F053'],
            ['openspec/foundation/extension.md', 'F016'], ['openspec/foundation/modules.md', 'F077'],
        ]
        self.assertEqual(result['samples'], expected)
        self.assertEqual(result['candidate_sha256'], 'e5f5dac5ff61914ec6dc1ca8106af4549d3f9e13c1a15b6769adaa4a792829c1')
        self.assertEqual(result['N'], 100)
        self.assertEqual(result['sample_size'], 10)
        self.assertEqual(self.sample(list(reversed(candidates))), result)

    def test_small_and_empty_pools_never_invent_facts_or_pass_verdicts(self):
        small = self.sample(self.candidates(3))
        self.assertEqual(small['sample_size'], 3)
        empty = self.sample([])
        self.assertEqual(empty['N'], 0)
        self.assertEqual(empty['D'], 0)
        self.assertEqual(empty['samples'], [])
        self.assertEqual(empty['status'], 'no_facts')
        self.assertNotIn('passed', empty.values())

    def test_readme_and_errata_do_not_duplicate_the_fact_pool(self):
        candidates = self.candidates(7)
        result = self.sample(candidates + [('openspec/foundation/README.md', 'baseline'),
                                         ('openspec/foundation/errata.md', 'E-0001')])
        self.assertEqual(result, self.sample(candidates))

    def test_utf8_sorting_and_unambiguous_candidate_encoding(self):
        candidates = [('openspec/foundation/机制.md', 'F002'),
                      ('openspec/foundation/réseau.md', 'F001')]
        result = self.sample(candidates)
        self.assertEqual(result['candidates'], list(reversed([list(row) for row in candidates])))
        self.assertEqual(result, self.sample(list(reversed(candidates))))
        for bad in (('bad\tpath.md', 'F1'), ('ok.md', 'F\n1'), ('../escape.md', 'F1'),
                    ('/absolute.md', 'F1'), ('C:/absolute.md', 'F1'), ('ok.md', 'F\r1')):
            with self.subTest(candidate=bad), self.assertRaises(ValueError):
                self.sample([bad])
        with self.assertRaises(ValueError):
            self.sample([candidates[0], candidates[0]])

    def test_seed_is_one_recorded_lowercase_16_byte_value(self):
        for seed in ('', '0' * 31, '0' * 33, 'G' * 32, 'A' * 32):
            with self.subTest(seed=seed), self.assertRaises(ValueError):
                self.sample(self.candidates(7), seed)

    def test_recipe_runs_in_memory_with_json_stdin_without_a_script_file(self):
        reference = SKILLS_ROOT / 'foundation-survey/references/source-and-sampling.md'
        code = re.findall(r'```python\n(.*?)\n```', reference.read_text(encoding='utf-8'), re.S)[0]
        request = {'candidates': self.candidates(7), 'seed': SEED}
        result = subprocess.run([sys.executable, '-I', '-c', code],
                                input=json.dumps(request), capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout), self.sample(self.candidates(7)))


if __name__ == '__main__':
    unittest.main()
