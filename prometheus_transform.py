"""Reshape a narrowly recognized one-line VM dispatcher using source spans.

This keeps VM transitions and leaf code intact. It does not reconstruct the
original program, and output should be considered experimental.
"""

from vendor.unprom import ast_nodes as A
from vendor.unprom.astutil import as_int, unparen
from vendor.unprom.lexer import tokenize
from vendor.unprom.parser import Parser
from vendor.unprom.vm import find_container

MAX_SOURCE_BYTES = 100_000


class LocationParser(Parser):
    def _block(self):
        start = self.cur.col - 1
        node = super()._block()
        node.source_start = start
        node.source_end = self.cur.col - 1
        return node


def flatten_dispatcher(source: str) -> tuple[str, int] | None:
    """Return a partial transformation, or None when the shape is unsupported."""
    if not (1000 <= len(source.encode('utf-8')) <= MAX_SOURCE_BYTES):
        return None
    # Lexer columns are source offsets only for a single line. Also reject all
    # comments conservatively, including '--' inside strings.
    if '\n' in source or '\r' in source or '--' in source:
        return None
    chunk = LocationParser(tokenize(source)).parse_chunk()
    container = find_container(chunk)
    if container is None or len(container.container_fn.params) != 4 or not container.ret_name:
        return None
    root = container.while_stmt.body
    leaves, limits = [], []

    def walk(block):
        if len(block.stmts) == 1 and isinstance(block.stmts[0], A.If):
            branch = block.stmts[0]
            if len(branch.clauses) == 1 and branch.orelse is not None:
                cond = unparen(branch.clauses[0][0])
                if (isinstance(cond, A.BinOp) and cond.op == '<'
                        and isinstance(unparen(cond.left), A.Name)
                        and unparen(cond.left).name == container.pos_name
                        and as_int(cond.right) is not None):
                    walk(branch.clauses[0][1])
                    limits.append(as_int(cond.right))
                    walk(branch.orelse)
                    return
        leaves.append(block)

    walk(root)
    if len(leaves) < 3 or len(leaves) > 512 or limits != sorted(set(limits)):
        return None
    parts = []
    for i, leaf in enumerate(leaves):
        if i < len(limits):
            parts.append(('if ' if i == 0 else ' elseif ') + container.pos_name
                         + ' < ' + str(limits[i]) + ' then ')
        else:
            parts.append(' else ')
        parts.append(source[leaf.source_start:leaf.source_end])
        parts.append(' ')
    parts.append('end ')
    output = source[:root.source_start] + ''.join(parts) + source[root.source_end:]
    if len(output.encode('utf-8')) > MAX_SOURCE_BYTES * 2:
        return None
    return output, len(leaves)
