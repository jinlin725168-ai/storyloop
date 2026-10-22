"""Conservative AST tracking of run origins, aliases and persisted copies (not eval)."""
import ast


def call_name(node):
    return node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else ''


def literal(node, value):
    return isinstance(node, ast.Constant) and node.value == value


def names(node):
    return {item.id for item in ast.walk(node) if isinstance(item, ast.Name)}


def run_location(node, paths):
    return bool(names(node) & paths) or any(
        isinstance(item, ast.Call) and call_name(item.func) == 'run_path'
        or isinstance(item, ast.Constant) and isinstance(item.value, str)
        and ('state/runs' in item.value.replace('\\', '/') or item.value == 'runs')
        for item in ast.walk(node))


def contains_status(node, definitions, visited=None):
    visited = set(visited or ())
    if isinstance(node, ast.Name) and node.id in definitions and node.id not in visited:
        return contains_status(definitions[node.id], definitions, visited | {node.id})
    if isinstance(node, ast.Dict):
        return any(literal(key, 'status') or key is None and contains_status(value, definitions, visited)
                   for key, value in zip(node.keys, node.values))
    if isinstance(node, ast.Call) and call_name(node.func) == 'dict':
        return any(key.arg == 'status' or key.arg is None and contains_status(key.value, definitions, visited)
                   for key in node.keywords) or any(contains_status(arg, definitions, visited) for arg in node.args)
    return False


def scope_nodes(function):
    """Walk one lexical scope; nested functions own independent local names."""
    pending = list(function.body)
    while pending:
        node = pending.pop()
        yield node
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            pending.extend(ast.iter_child_nodes(node))


def assignments(function):
    return [(node.targets, node.value) if isinstance(node, ast.Assign) else ([node.target], node.value)
            for node in scope_nodes(function) if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value]


def origins(function, initial_paths=(), nonruns=()):
    nodes = assignments(function)
    definitions = {target.id: value for targets, value in nodes for target in targets
                   if isinstance(target, ast.Name)}
    paths, runs = set(initial_paths), {arg.arg for arg in function.args.args if arg.arg in {'state', 'run', 'old_run'} and arg.arg not in nonruns}
    changed = True
    while changed:
        before = (set(paths), set(runs))
        for targets, value in nodes:
            targets = {target.id for target in targets if isinstance(target, ast.Name)}
            if run_location(value, paths):
                paths |= targets
            loaded = isinstance(value, ast.Call) and (
                call_name(value.func) in {'load_run_state', '_new_run_state'}
                or call_name(value.func) == 'load_json' and value.args and run_location(value.args[0], paths))
            copied = (isinstance(value, ast.Name) and value.id in runs
                      or isinstance(value, ast.Dict) and any(key is None and isinstance(val, ast.Name)
                          and val.id in runs for key, val in zip(value.keys, value.values))
                      or isinstance(value, ast.Call) and call_name(value.func) == 'dict'
                      and bool(value.args) and isinstance(value.args[0], ast.Name) and value.args[0].id in runs)
            if loaded or copied:
                runs |= targets
            if targets & runs and isinstance(value, ast.Name):
                runs.add(value.id)
        for node in scope_nodes(function):
            if isinstance(node, ast.Call) and call_name(node.func) in {'save_run', 'transition'}:
                if len(node.args) >= 2 and isinstance(node.args[1], ast.Name):
                    runs.add(node.args[1].id)
        changed = before != (paths, runs)
    return runs, paths, definitions


def status_target(node, runs):
    return (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)
            and node.value.id in runs and literal(node.slice, 'status'))


def mutation(node, runs, definitions):
    if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Delete)):
        targets = node.targets if isinstance(node, (ast.Assign, ast.Delete)) else [node.target]
        if any(status_target(target, runs) for target in targets):
            return True
        if isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            return node.target.id in runs and contains_status(node.value, definitions)
        value = getattr(node, 'value', None)
        return bool(value and contains_status(value, definitions)
                    and any(isinstance(target, ast.Name) and target.id in runs for target in targets))
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return False
    receiver = node.func.value
    if not isinstance(receiver, ast.Name) or receiver.id not in runs:
        return False
    if node.func.attr in {'pop', 'setdefault', '__setitem__', '__delitem__'}:
        return bool(node.args and literal(node.args[0], 'status'))
    return node.func.attr == 'update' and (
        any(contains_status(arg, definitions) for arg in node.args)
        or any(key.arg == 'status' or key.arg is None and contains_status(key.value, definitions)
               for key in node.keywords))


def local_path_parameters(functions):
    """Propagate run-file paths through ordinary calls within this module."""
    by_name = {function.name: function for function in functions}
    parameters = {name: set() for name in by_name}
    changed = True
    while changed:
        changed = False
        for function in functions:
            _, paths, _ = origins(function, parameters[function.name])
            for node in scope_nodes(function):
                if not isinstance(node, ast.Call) or call_name(node.func) not in by_name:
                    continue
                callee = by_name[call_name(node.func)]
                for argument, parameter in zip(node.args, callee.args.args):
                    if run_location(argument, paths) and parameter.arg not in parameters[callee.name]:
                        parameters[callee.name].add(parameter.arg)
                        changed = True
    return parameters


def saved_returners(functions):
    """Trace local producers whose return value flows into the run saver."""
    result = {'_new_run_state', 'load_run_state'}
    for function in functions:
        runs, _, definitions = origins(function)
        values = [definitions[name] for name in runs if name in definitions]
        values += [node.args[1] for node in scope_nodes(function) if isinstance(node, ast.Call)
                   and call_name(node.func) == 'save_run' and len(node.args) > 1]
        result |= {call_name(value.func) for value in values if isinstance(value, ast.Call)}
    return result


def known_substate_parameters(functions):
    """Explicit local calls with subrecords override the conventional run-parameter spelling."""
    by_name = {function.name: function for function in functions}
    uses = {}
    for function in functions:
        for node in scope_nodes(function):
            if isinstance(node, ast.Call) and call_name(node.func) in by_name:
                callee = by_name[call_name(node.func)]
                for arg, param in zip(node.args, callee.args.args):
                    uses.setdefault((callee.name, param.arg), []).append(isinstance(arg, ast.Subscript))
    return {name: {param for (callee, param), observations in uses.items()
                   if callee == name and all(observations)} for name in by_name}


def status_writes(source, module):
    """Return actionable function/line diagnostics; nested views are separate entities."""
    found = []
    functions = [node for node in ast.walk(ast.parse(source))
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    parameters = local_path_parameters(functions)
    returners = saved_returners(functions)
    nonruns = known_substate_parameters(functions)
    for function in functions:
        if module == 'run_transitions' and function.name == 'transition':
            continue
        runs, paths, definitions = origins(function, parameters[function.name], nonruns[function.name])
        for node in scope_nodes(function):
            reason = 'run status mutation' if mutation(node, runs, definitions) else None
            if isinstance(node, ast.Return) and function.name in returners:
                if node.value and contains_status(node.value, definitions):
                    reason = 'saved run constructor bypasses transition'
            if isinstance(node, ast.Call) and call_name(node.func) == 'write_json':
                path = node.args[0] if node.args else next((key.value for key in node.keywords if key.arg == 'path'), None)
                if path is not None and run_location(path, paths) and (module, function.name) != ('run_state', 'save_run'):
                    reason = 'run persistence bypasses save_run'
            if isinstance(node, ast.Call) and call_name(node.func) == 'save_run' and len(node.args) > 1:
                if contains_status(node.args[1], definitions):
                    reason = 'persisted run copy supplies status'
            if isinstance(node, ast.Call) and call_name(node.func) == '_save' and len(node.args) > 1:
                value = node.args[1]
                if isinstance(value, ast.Dict) and any(literal(key, 'status') and isinstance(val, ast.Constant)
                    and val.value in {'PASSED', 'SUPERSEDED'} for key, val in zip(value.keys, value.values)):
                    reason = 'aggregate lifecycle status bypasses transition'
            if reason:
                found.append(f'{module}.{function.name}:{node.lineno}: {reason}')
    return sorted(set(found))
