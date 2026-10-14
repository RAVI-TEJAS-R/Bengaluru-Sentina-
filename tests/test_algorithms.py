import unittest
from algos import AC, Signer, astar, binary_search, canon


class AlgorithmTests(unittest.TestCase):
    def test_astar_finds_lowest_cost_path(self):
        # 0 -> 1 -> 2 costs 2; direct 0 -> 2 costs 5.
        points = {0: (12.0, 77.0), 1: (12.0, 77.0), 2: (12.0, 77.0)}
        adjacency = {0: [(1, 0), (2, 2)], 1: [(2, 1)], 2: []}
        weights = {0: 1, 1: 1, 2: 5}
        result = astar(adjacency, points, lambda edge: weights[edge], 0, 2)
        self.assertIsNotNone(result)
        self.assertEqual(result[0], [0, 1, 2])
        self.assertAlmostEqual(result[2], 2)

    def test_binary_search_found_and_missing(self):
        records = [{"id": "a"}, {"id": "c"}, {"id": "f"}, {"id": "z"}]
        self.assertEqual(binary_search(records, "f")["id"], "f")
        self.assertIsNone(binary_search(records, "b"))

    def test_aho_corasick_finds_multiple_patterns(self):
        matcher = AC(["KA0100", "KA0200", "KA0300"])
        self.assertEqual(matcher.find("event:KA0100;another:KA0300"), {"KA0100", "KA0300"})

    def test_plate_canonicalization(self):
        self.assertEqual(canon("ka01ab1234"), canon("KAO1A81234"))

    def test_rsa_signature_verifies_and_detects_tampering(self):
        signer = Signer()
        payload = b"demo incident record"
        signature = signer.sign(payload)
        self.assertTrue(signer.verify(payload, signature))
        self.assertFalse(signer.verify(payload + b" changed", signature))


if __name__ == "__main__":
    unittest.main()
