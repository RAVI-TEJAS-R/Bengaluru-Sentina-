"""Core algorithms used by BLR-Sentinel.

A*: traffic-weighted graph search
Binary search: retrieval from sorted incident records
Aho-Corasick: multi-pattern plate matching
RSA-PSS/SHA-256: record signatures (implemented by a vetted crypto library)
"""
import hashlib
import heapq
import math
from collections import deque
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


def hav(a, b):
    """Great-circle distance in metres between (lat, lon) coordinates."""
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    x = math.sin((la2-la1)/2)**2 + math.cos(la1)*math.cos(la2)*math.sin((lo2-lo1)/2)**2
    return 12742000 * math.asin(math.sqrt(min(1.0, x)))


def astar(adj, P, w, s, t, vmax=16.7):
    """A* on a non-negative weighted graph. w(edge_id) returns edge cost."""
    h = lambda n: hav(P[n], P[t]) / vmax
    g, prev, done, pq = {s: 0.0}, {}, set(), [(h(s), s)]
    while pq:
        _, u = heapq.heappop(pq)
        if u in done:
            continue
        if u == t:
            break
        done.add(u)
        for v, edge_id in adj.get(u, []):
            nd = g[u] + max(0.001, float(w(edge_id)))
            if nd < g.get(v, float("inf")):
                g[v] = nd
                prev[v] = (u, edge_id)
                heapq.heappush(pq, (nd + h(v), v))
    if t not in g:
        return None
    path, edges, node = [t], [], t
    while node != s:
        node, edge_id = prev[node]
        path.append(node)
        edges.append(edge_id)
    return path[::-1], edges[::-1], g[t]


def k_routes(adj, P, w, s, t, k=3, pen=1.5):
    """Generate diverse alternatives with a transparent edge-penalty heuristic."""
    penalties, out, seen = {}, [], set()
    for _ in range(k):
        result = astar(adj, P, lambda e: w(e) * penalties.get(e, 1.0), s, t)
        if not result:
            break
        signature = tuple(result[1])
        if signature not in seen:
            seen.add(signature)
            out.append((result[0], result[1], sum(w(e) for e in result[1])))
        for edge_id in result[1]:
            penalties[edge_id] = penalties.get(edge_id, 1.0) * pen
    return out


def binary_search(records, target, key=lambda record: record["id"]):
    """Iterative binary search over records sorted ascending by key.

    Returns the matching record or None. O(log n) comparisons.
    """
    lo, hi = 0, len(records) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        value = key(records[mid])
        if value == target:
            return records[mid]
        if value < target:
            lo = mid + 1
        else:
            hi = mid - 1
    return None


class AC:
    """Aho-Corasick: match all watchlist patterns in one pass over the text."""
    def __init__(self, words):
        self.g, self.f, self.o = [{}], [0], [set()]
        for word in words:
            node = 0
            for char in word:
                if char not in self.g[node]:
                    self.g.append({})
                    self.f.append(0)
                    self.o.append(set())
                    self.g[node][char] = len(self.g) - 1
                node = self.g[node][char]
            self.o[node].add(word)
        queue = deque(self.g[0].values())
        while queue:
            node = queue.popleft()
            for char, child in self.g[node].items():
                fail = self.f[node]
                while fail and char not in self.g[fail]:
                    fail = self.f[fail]
                self.f[child] = self.g[fail].get(char, 0)
                self.o[child] |= self.o[self.f[child]]
                queue.append(child)

    def find(self, text):
        node, hits = 0, set()
        for char in text:
            while node and char not in self.g[node]:
                node = self.f[node]
            node = self.g[node].get(char, 0)
            hits |= self.o[node]
        return hits


# OCR ambiguity normalization is deliberately used only for matching, not identity.
CONF = str.maketrans({"O": "0", "I": "1", "B": "8", "S": "5", "Z": "2", "D": "0", "Q": "0"})
def canon(plate):
    return "".join(ch for ch in plate.upper().translate(CONF) if ch.isalnum())


class Signer:
    """RSA-PSS signatures using the maintained cryptography implementation."""
    def __init__(self, bits=2048):
        self.private_key = rsa.generate_private_key(public_exponent=65537, key_size=bits)
        self.public_key = self.private_key.public_key()

    def sign(self, message):
        return self.private_key.sign(
            message,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH),
            hashes.SHA256(),
        )

    def verify(self, message, signature):
        try:
            self.public_key.verify(
                signature, message,
                padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH),
                hashes.SHA256(),
            )
            return True
        except Exception:
            return False

    def public_pem(self):
        return self.public_key.public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("ascii")
