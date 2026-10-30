"""Emit readable Lua from explicit operations and a validated control-flow graph."""
from dataclasses import dataclass


class CannotStructure(ValueError):
    pass


@dataclass
class Step:
    address: int
    code: str
    targets: tuple[int, ...]
    condition: str | None = None
    dynamic: bool = False


def blocks(steps, exit_address):
    if not steps:
        return {}
    addresses = {s.address for s in steps}
    leaders = {steps[0].address}
    for i, step in enumerate(steps):
        following = steps[i + 1].address if i + 1 < len(steps) else exit_address
        if any(t not in addresses and t != exit_address for t in step.targets):
            raise ValueError('Jump does not point to an instruction boundary')
        if step.condition or step.dynamic or step.targets != (following,):
            leaders.update(step.targets)
            leaders.add(following)
    result = {}
    group = []
    for step in steps:
        if group and step.address in leaders:
            last = group[-1]
            result[group[0].address] = Step(group[0].address, '\n'.join(s.code for s in group if s.code),
                                                  last.targets, last.condition, last.dynamic)
            group = []
        group.append(step)
    if group:
        last = group[-1]
        result[group[0].address] = Step(group[0].address, '\n'.join(s.code for s in group if s.code),
                                              last.targets, last.condition, last.dynamic)
    reachable = set()
    todo = [steps[0].address]
    while todo:
        n = todo.pop()
        if n == exit_address or n in reachable:
            continue
        reachable.add(n)
        todo.extend(result[n].targets)
    return {n: s for n, s in result.items() if n in reachable}


def indent(code, depth=1):
    return '\n'.join('    ' * depth + line if line else '' for line in code.splitlines())


def _structured(graph, entry, exit_address):
    if any(s.dynamic for s in graph.values()) or len(graph) > 400:
        raise CannotStructure()
    nodes = set(graph) | {exit_address}
    preds = {n: set() for n in nodes}
    for n, s in graph.items():
        for t in s.targets:
            preds[t].add(n)
    can_exit, todo = {exit_address}, [exit_address]
    while todo:
        for n in preds[todo.pop()]:
            if n not in can_exit:
                can_exit.add(n)
                todo.append(n)
    if can_exit != nodes:
        raise CannotStructure()
    dominators = {n: ({n} if n == entry else set(nodes)) for n in nodes}
    post = {n: ({n} if n == exit_address else set(nodes)) for n in nodes}
    for _ in range(len(nodes) + 1):
        changed = False
        for n in nodes - {entry}:
            d = {n} | set.intersection(*(dominators[p] for p in preds[n])) if preds[n] else {n}
            if d != dominators[n]:
                dominators[n] = d
                changed = True
        for n, s in graph.items():
            p = {n} | set.intersection(*(post[t] for t in s.targets)) if s.targets else {n, exit_address}
            if p != post[n]:
                post[n] = p
                changed = True
        if not changed:
            break
    else:
        raise CannotStructure()
    loops = {}
    for n, s in graph.items():
        for header in s.targets:
            if header in dominators[n] and header != exit_address:
                loop = {header, n}
                todo = [] if n == header else [n]
                while todo:
                    for p in preds[todo.pop()]:
                        if p not in loop:
                            loop.add(p)
                            todo.append(p)
                loops.setdefault(header, set()).update(loop)
    loop_exits = {}
    for header, members in loops.items():
        exits = {t for n in members for t in graph[n].targets if t not in members}
        if len(exits) != 1 or len(graph[header].targets) != 2:
            raise CannotStructure()
        out = next(iter(exits))
        if out not in graph[header].targets:
            raise CannotStructure()
        loop_exits[header] = out
    emitted = 0

    def walk(n, stop, active=(), path=frozenset(), depth=0):
        nonlocal emitted
        if depth > 80:
            raise CannotStructure()
        lines = []
        seen = set(path)
        while n != stop and n != exit_address:
            if active and n == active[-1][0]:
                lines.append('continue')
                return '\n'.join(lines)
            if active and n == active[-1][1]:
                lines.append('break')
                return '\n'.join(lines)
            if n in seen:
                raise CannotStructure()
            seen.add(n)
            emitted += 1
            if emitted > len(graph) * 4 + 10:
                raise CannotStructure()
            step = graph[n]
            if n in loops:
                out = loop_exits[n]
                yes, no = step.targets
                inside = no if yes == out else yes
                condition = step.condition if yes == out else f'not ({step.condition})'
                body = [step.code, f'if {condition} then\n    break\nend',
                        walk(inside, n, active + ((n, out),), frozenset(), depth + 1)]
                lines.append('while true do\n' + indent('\n'.join(x for x in body if x)) + '\nend')
                n = out
                continue
            if step.code:
                lines.append(step.code)
            if step.condition:
                yes, no = step.targets
                common = post[yes] & post[no]
                join = max(common, key=lambda x: len(post[x]))
                first = walk(yes, join, active, frozenset(seen), depth + 1)
                second = walk(no, join, active, frozenset(seen), depth + 1)
                lines.append(f'if {step.condition} then\n' + indent(first) +
                             ('\nelse\n' + indent(second) if second else '') + '\nend')
                n = join
            elif step.targets:
                n = step.targets[0]
            else:
                return '\n'.join(lines)
        return '\n'.join(lines)
    return walk(entry, exit_address)


def render(steps, exit_address):
    """Return source and whether all branches were structured without a state loop."""
    graph = blocks(steps, exit_address)
    if not graph:
        return '', True
    entry = steps[0].address
    try:
        return _structured(graph, entry, exit_address), True
    except (CannotStructure, RecursionError):
        pass
    items = sorted(graph)

    def dispatch(keys):
        if len(keys) == 1:
            step = graph[keys[0]]
            body = [f'-- block {step.address}', step.code]
            if step.condition:
                body.append(f'if {step.condition} then state = {step.targets[0]} else state = {step.targets[1]} end')
            elif step.targets and not step.dynamic:
                body.append(f'state = {step.targets[0]}')
            elif not step.targets:
                body.append('return')
            return '\n'.join(x for x in body if x)
        middle = len(keys) // 2
        return (f'if state < {keys[middle]} then\n' + indent(dispatch(keys[:middle])) +
                '\nelse\n' + indent(dispatch(keys[middle:])) + '\nend')
    # Exit is a separate dispatch leaf, so all ordinary fallthrough paths return.
    graph[exit_address] = Step(exit_address, '', ())
    return f'local state = {entry}\nwhile true do\n' + indent(dispatch(sorted(graph))) + '\nend', False
