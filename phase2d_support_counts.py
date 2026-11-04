"""Experimental support-count propagation for source-label updates."""

from collections import deque


class SupportCountPropagator:
    def __init__(self, graph):
        self.graph = graph
        n = graph.n

        self.direct_s = [0] * n
        self.direct_i = [0] * n
        self.propagated_s = [0] * n
        self.propagated_i = [0] * n

        # Per-node dictionary: bit -> number of direct/incoming-edge supports.
        self.support_s = [{} for _ in range(n)]
        self.support_i = [{} for _ in range(n)]

    def apply(self, operation, kind, node, bit):
        if operation not in {"add", "remove"}:
            raise ValueError(f"Unknown operation: {operation}")
        if kind not in {"sensitive", "untrusted"}:
            raise ValueError(f"Unknown label kind: {kind}")
        if not 0 <= node < self.graph.n:
            raise IndexError(node)
        if bit <= 0 or bit & (bit - 1):
            raise ValueError("bit must be a positive power of two")

        if kind == "sensitive":
            direct = self.direct_s
            propagated = self.propagated_s
            supports = self.support_s
        else:
            direct = self.direct_i
            propagated = self.propagated_i
            supports = self.support_i

        nodes_examined = 1
        edges_examined = 0

        if operation == "add":
            # A repeated direct source at the same node is idempotent.
            if direct[node] & bit:
                return nodes_examined, edges_examined

            direct[node] |= bit
            supports[node][bit] = supports[node].get(bit, 0) + 1

            # Another parent may already support this bit. The output has not
            # changed, so there is no need to notify downstream nodes.
            if propagated[node] & bit:
                return nodes_examined, edges_examined

            propagated[node] |= bit
            queue = deque([node])

            while queue:
                parent = queue.popleft()

                for child in self.graph.children[parent]:
                    edges_examined += 1
                    nodes_examined += 1

                    old_count = supports[child].get(bit, 0)
                    supports[child][bit] = old_count + 1

                    # Propagate further only when this node's output changes.
                    if not (propagated[child] & bit):
                        propagated[child] |= bit
                        queue.append(child)

            return nodes_examined, edges_examined

        # Removal: first remove the direct support.
        if not (direct[node] & bit):
            return nodes_examined, edges_examined

        direct[node] &= ~bit
        count = supports[node].get(bit, 0) - 1

        if count < 0:
            raise AssertionError("Negative support count")

        if count:
            supports[node][bit] = count
            return nodes_examined, edges_examined

        supports[node].pop(bit, None)

        # The node lost its last support, so its output bit disappears.
        if not (propagated[node] & bit):
            raise AssertionError("Support count and propagated mask disagree")

        propagated[node] &= ~bit
        queue = deque([node])

        while queue:
            parent = queue.popleft()

            for child in self.graph.children[parent]:
                edges_examined += 1
                nodes_examined += 1

                child_count = supports[child].get(bit, 0) - 1

                if child_count < 0:
                    raise AssertionError("Negative support count")

                if child_count == 0:
                    supports[child].pop(bit, None)

                    if propagated[child] & bit:
                        propagated[child] &= ~bit
                        queue.append(child)
                else:
                    supports[child][bit] = child_count

        return nodes_examined, edges_examined
