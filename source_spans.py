"""Track exact source ranges without serializing Lua back through a printer."""
from vendor.unprom.parser import Parser
from vendor.unprom.lexer import tokenize


class SourceParser(Parser):
    def __init__(self, source):
        self.source = source
        self.starts = [0] + [i + 1 for i, char in enumerate(source) if char == '\n']
        self.expressions = []
        self.layout = {}
        self.depth = 0
        super().__init__(tokenize(source))

    def offset(self, token=None):
        token = self.cur if token is None else token
        return self.starts[token.line - 1] + token.col - 1

    def _expr(self, min_bp=0):
        start = self.offset()
        node = super()._expr(min_bp)
        # End at the last consumed token, excluding trailing comments/trivia.
        token = self.toks[self.p - 1]
        end = self.offset(token) + len(token.raw if token.raw is not None else token.value)
        self.expressions.append((start, end, node))
        return node

    def _block(self):
        start = self.offset()
        self.depth += 1
        try:
            node = super()._block()
            node.source_start = start
            node.source_end = self.offset()
            self.layout[self.offset()] = max(0, self.depth - 2)
            return node
        finally:
            self.depth -= 1

    def _statement(self):
        self.layout[self.offset()] = max(0, self.depth - 1)
        return super()._statement()

    def _return_stat(self):
        self.layout[self.offset()] = max(0, self.depth - 1)
        return super()._return_stat()
