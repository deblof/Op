"""Reshape a narrowly recognized VM dispatcher using source spans.

This keeps VM transitions and leaf code intact. It does not reconstruct the
original program, and output should be considered experimental.
"""

from vendor.unprom import ast_nodes as A
from vendor.unprom.astutil import as_int, unparen
from vendor.unprom.vm import find_container
from source_spans import SourceParser

MAX_SOURCE_BYTES = 100_000


def flatten_dispatcher(source: str) -> tuple[str, int] | None:
    """Return a partial transformation, or None when the shape is unsupported."""
    if not (1000 <= len(source.encode('utf-8')) <= MAX_SOURCE_BYTES):
        return None
    # Absolute offsets also work with comments, Unicode and multiline bodies.
    code = source
    chunk = SourceParser(code).parse_chunk()
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
            parts.append(('if ' if i == 0 else '\nelseif ') + container.pos_name
                         + ' < ' + str(limits[i]) + ' then\n    ')
        else:
            parts.append('\nelse\n    ')
        parts.append(code[leaf.source_start:leaf.source_end])
        parts.append(' ')
    parts.append('\nend ')
    output = code[:root.source_start] + ''.join(parts) + code[root.source_end:]
    if len(output.encode('utf-8')) > MAX_SOURCE_BYTES * 2:
        return None
    return output, len(leaves)
