"""Independent structural projections for the stage-0 and bootstrap parsers."""

import ast as python_ast
from dataclasses import dataclass, field

from jack import ast_nodes as ast
from jack.parser import parse
from jack.source_model import TypeReference


def n(kind, **fields):
    return {'kind': kind, **fields}


def name(value):
    return n('name', value=value)


def apply(callee, arguments):
    return n('call', callee=callee, arguments=arguments)


def modifiers(value):
    return sorted(key for key in ('public', 'comptime', 'extern', 'unsafe',
                                  'constant', 'comptime_initializer', 'raises_inferred')
                  if getattr(value, key, False))


def python_type(value):
    result = name(value.name)
    if value.arguments:
        result = apply(result, [python_node(arg) for arg in value.arguments])
    if value.is_slice:
        result = n('slice_type', element=result)
    if value.array_size is not None:
        result = n('array_type', element=result, extent=python_node(value.array_size))
    if value.borrow:
        result = n('borrow_type', mode=value.borrow, target=result)
    if value.pointer_mode:
        result = n('pointer_type', mode=value.pointer_mode,
                   nullable=value.nullable, target=result)
    return result


def python_node(value):
    result = _python_node(value)
    if (isinstance(value, ast.Statement) and value.comptime
            and not isinstance(value, (ast.VariableDeclaration, ast.FunctionDeclaration,
                                       ast.TypeDeclaration))):
        return n('comptime', statement=result)
    return result


def _python_node(value):
    if value is None:
        return None
    if isinstance(value, list):
        return [python_node(item) for item in value]
    if isinstance(value, TypeReference):
        return python_type(value)
    if isinstance(value, (bool, int, float, str)):
        return n('literal', value=value)
    if isinstance(value, ast.ModuleDeclaration):
        return n('module', path=value.name)
    if isinstance(value, ast.ImportDeclaration):
        return n('import', path=value.module_name, alias=value.alias,
                 symbols=value.symbols or [])
    if isinstance(value, ast.VariableDeclaration):
        return n('variable', name=value.name, type=python_type(value.type),
                 initializer=python_node(value.expr), arguments=python_node(value.constructor_args),
                 constraints=python_node(value.constraints), passing=value.passing_mode,
                 modifiers=modifiers(value), abi=value.abi)
    if isinstance(value, ast.FunctionDeclaration):
        parameters = ([value.self_parameter] if value.self_parameter else []) + value.parameters
        return n('function', name=value.name, parameters=python_node(parameters),
                 return_type=python_type(value.return_type), raises=python_node(value.raises),
                 body=python_node(value.body), modifiers=modifiers(value), abi=value.abi)
    if isinstance(value, ast.TypeDeclaration):
        return n('struct', name=value.name, parameters=python_node(value.parameters),
                 fields=python_node(value.fields), methods=python_node(value.methods),
                 modifiers=modifiers(value), abi=value.abi)
    if isinstance(value, ast.EnumDeclaration):
        return n('union', name=value.name, parameters=python_node(value.parameters),
                 variants=python_node(value.variants), methods=python_node(value.methods),
                 modifiers=modifiers(value))
    if isinstance(value, ast.EnumVariant):
        return n('variant', name=value.name, parameters=python_node(value.parameters))
    if isinstance(value, ast.InterfaceDeclaration):
        return n('interface', name=value.name, methods=python_node(value.methods),
                 modifiers=modifiers(value))
    if isinstance(value, ast.ImplementationDeclaration):
        return n('implementation', target=name(value.type_name),
                 parameters=python_node(value.parameters), interface=python_type(value.interface),
                 methods=python_node(value.methods), uses=[item.name for item in value.uses])
    if isinstance(value, ast.ViewDeclaration):
        return n('view', name=value.name, fields=python_node(value.fields), modifiers=modifiers(value))
    if isinstance(value, ast.ViewField):
        return n('view_field', name=value.name, type=python_type(value.type), mode=value.mode)
    if isinstance(value, ast.VariableExpression):
        return name(value.name)
    if isinstance(value, ast.TypeExpression):
        return python_type(value.type_ref)
    if isinstance(value, ast.LiteralExpression):
        return n('literal', value=value.value)
    if isinstance(value, ast.CompositeExpression):
        return n('binary', operator=value.operator, left=python_node(value.left), right=python_node(value.right))
    if isinstance(value, ast.UnaryExpression):
        return n('unary', operator=value.operator, operand=python_node(value.expr))
    if isinstance(value, ast.BorrowExpression):
        return n('borrow', mode=value.mode, operand=python_node(value.expr))
    if isinstance(value, ast.MoveExpression):
        return n('move', operand=python_node(value.expr))
    if isinstance(value, ast.DereferenceExpression):
        return n('dereference', operand=python_node(value.expr))
    if isinstance(value, ast.FunctionCall):
        return apply(name(value.function_name), python_node(value.parameters))
    if isinstance(value, ast.EnumVariantExpression):
        target = n('member', receiver=python_type(value.type_ref), name=value.variant_name)
        return target if value.arguments is None else apply(target, python_node(value.arguments))
    if isinstance(value, ast.StructLiteralExpression):
        return n('struct_literal', type=python_type(value.type_ref), fields=python_node(value.fields))
    if isinstance(value, ast.StructLiteralField):
        return n('field_value', name=value.name, value=python_node(value.expr))
    if isinstance(value, ast.IndexExpression):
        return n('index', target=python_node(value.target), index=python_node(value.index))
    if isinstance(value, ast.SliceExpression):
        return n('slice', target=python_node(value.target), start=python_node(value.start), end=python_node(value.end))
    if isinstance(value, ast.Assignment):
        # Python stores simple assignment targets as source text.
        target = value.name
        if isinstance(target, str):
            target = parse('print(' + target + ');')[0]
            target = target.expr or ast.VariableExpression(target.name)
        return n('assignment', target=python_node(target), value=python_node(value.expr))
    if isinstance(value, ast.If):
        return n('if', branches=python_node(value.branches), otherwise=python_node(value.else_body))
    if isinstance(value, ast.IfBranch):
        return n('if_branch', condition=python_node(value.condition), body=python_node(value.body))
    if isinstance(value, ast.While):
        return n('while', condition=python_node(value.condition), body=python_node(value.body))
    if isinstance(value, ast.For):
        return n('for', initializer=python_node(value.initializer), condition=python_node(value.condition),
                 update=python_node(value.update), body=python_node(value.body))
    if isinstance(value, ast.Try):
        return n('try', body=python_node(value.body), catches=python_node(value.catches))
    if isinstance(value, ast.CatchClause):
        return n('catch', name=value.name, error_type=python_type(value.error_type), body=python_node(value.body))
    if isinstance(value, ast.Block):
        return n('block', body=python_node(value.body))
    if isinstance(value, ast.UnsafeBlock):
        return n('unsafe', body=python_node(value.body))
    if isinstance(value, ast.Return):
        return n('return', value=python_node(value.expr))
    if isinstance(value, ast.Raise):
        return n('raise', error=python_node(value.expr))
    if isinstance(value, ast.Rethrow):
        return n('rethrow')
    if isinstance(value, ast.Print):
        return n('print', value=python_node(value.expr or ast.VariableExpression(value.name)))
    if isinstance(value, ast.Match):
        return n('match', scrutinee=python_node(value.scrutinee), arms=python_node(value.arms))
    if isinstance(value, ast.MatchArm):
        return n('arm', pattern=n('pattern', name=value.variant_name,
                                 bindings=[binding.name for binding in value.bindings]),
                 body=python_node(value.expr if value.expr is not None else value.body))
    if isinstance(value, ast.FormattedStringExpression):
        return n('formatted', parts=[n('text', value=part) if isinstance(part, str)
                                    else python_node(part) for part in value.parts])
    raise AssertionError(f'Unnormalized Python AST: {type(value).__name__}')


@dataclass
class DumpNode:
    kind: str
    start: int
    end: int
    fields: dict = field(default_factory=dict)
    values: dict = field(default_factory=dict)
    children: dict = field(default_factory=dict)


def read_dump(output):
    nodes, roots = {}, []
    for line in output.splitlines():
        record, *parts = line.split('\t')
        if record == 'root':
            assert int(parts[0]) == len(roots)
            roots.append(int(parts[1]))
        elif record == 'node':
            identity, kind, start, end = parts
            assert int(identity) not in nodes
            nodes[int(identity)] = DumpNode(kind, int(start), int(end))
        elif record == 'field':
            identity, key, start, end = parts
            nodes[int(identity)].fields[key] = (int(start), int(end))
        elif record == 'value':
            identity, key, value = parts
            nodes[int(identity)].values[key] = value
        elif record == 'child':
            identity, role, index, child = parts
            children = nodes[int(identity)].children.setdefault(role, [])
            assert int(index) == len(children), (identity, role, index)
            children.append(int(child))
        else:
            raise AssertionError(f'Unknown syntax record: {record}')
    return nodes, roots


def root_source_ranges(source, statements, output):
    """Compare independent source ranges, translating stage-0 character offsets."""
    offsets = [0]
    for character in source:
        offsets.append(offsets[-1] + len(character.encode('utf-8')))
    nodes, roots = read_dump(output)
    expected = [(offsets[item.span.start_offset], offsets[item.span.end_offset])
                for item in statements]
    actual = [(nodes[identity].start, nodes[identity].end) for identity in roots]
    return expected, actual


def bootstrap_nodes(source, output):
    source = source.encode('utf-8')
    nodes, roots = read_dump(output)

    def visit(identity):
        node = nodes[identity]
        kind = node.kind
        def text(key):
            start, end = node.fields.get(key, (0, 0))
            assert 0 <= start <= end <= len(source)
            return source[start:end].decode('utf-8')
        def many(role):
            return [visit(child) for child in node.children.get(role, [])]
        def one(role, default=None):
            items = many(role)
            assert len(items) <= 1, (kind, role, items)
            if items and role == 'body' and isinstance(items[0], dict) and items[0].get('kind') == 'block':
                return items[0]['body']
            return items[0] if items else default
        mods = sorted(key for key, value in node.values.items()
                      if value == 'true' and key not in {'consuming', 'constructor'})
        abi = python_ast.literal_eval(text('abi')) if text('abi') else None
        if kind == 'module_declaration':
            return n('module', path=text('path'))
        if kind == 'import_declaration':
            return n('import', path=text('path'), alias=text('alias') or None,
                     symbols=[item['value'] for item in many('symbols')])
        if kind in {'name_expression', 'named_type', 'import_symbol'}:
            return name(text('name'))
        if kind == 'generic_type':
            return apply(name(text('name')), many('arguments'))
        if kind in {'borrow_type', 'raw_pointer_type', 'nullable_pointer_type'}:
            mode = text('mode').replace('?', '').replace('*', '').replace('&', '').split()[0]
            target = one('target', name('self'))
            if kind == 'borrow_type':
                return n('borrow_type', mode=mode, target=target)
            return n('pointer_type', mode=mode, nullable=kind == 'nullable_pointer_type', target=target)
        if kind == 'array_type':
            return n(kind, element=one('element'), extent=one('extent'))
        if kind == 'slice_type':
            return n(kind, element=one('element'))
        if kind in {'variable_declaration', 'constant_declaration', 'parameter', 'field_declaration', 'comptime_type_declaration'}:
            return n('variable', name=text('name'), type=one('type', name('self') if text('name') == 'self' else name('type')),
                     initializer=one('initializer'), arguments=many('arguments'), constraints=many('constraints'),
                     passing='move' if node.values.get('consuming') == 'true' else 'copy', modifiers=mods, abi=abi)
        if kind == 'function_declaration':
            return n('function', name=text('name'), parameters=many('parameters'),
                     return_type=one('return_type', name('void')), raises=many('raises'),
                     body=one('body', []), modifiers=mods, abi=abi)
        if kind in {'struct_declaration', 'union_declaration', 'interface_declaration', 'view_declaration'}:
            body = one('body', [])
            result = n(kind.removesuffix('_declaration'), name=text('name'), modifiers=mods)
            if kind in {'struct_declaration', 'union_declaration'}:
                result['parameters'] = many('parameters')
            if kind != 'view_declaration':
                result['methods'] = [item for item in body if item['kind'] == 'function']
            if kind in {'struct_declaration', 'view_declaration'}:
                result['fields'] = [item for item in body if item['kind'] != 'function']
            if kind == 'struct_declaration':
                result['abi'] = abi
            if kind == 'union_declaration':
                result['variants'] = [item for item in body if item['kind'] == 'variant']
            return result
        if kind == 'extern_type_declaration':
            return n('struct', name=text('name'), parameters=[], fields=[], methods=[], modifiers=mods, abi=abi)
        if kind == 'union_variant':
            return n('variant', name=text('name'), parameters=many('parameters'))
        if kind == 'implementation_declaration':
            target = one('target')
            parameters = target['arguments'] if target['kind'] == 'call' else []
            if target['kind'] == 'call':
                target = target['callee']
            body = one('body', [])
            return n('implementation', target=target, parameters=parameters, interface=one('interface_type'),
                     methods=[item for item in body if item['kind'] == 'function'],
                     uses=[item['name'] for item in body if item['kind'] == 'use'])
        if kind == 'implementation_use':
            return n('use', name=text('name'))
        if kind == 'view_field':
            return n('view_field', name=text('name'), type=one('type'), mode=text('mode'))
        if kind == 'block_statement':
            return n('block', body=many('statements'))
        if kind == 'comptime_statement':
            return n('comptime', statement=one('statement'))
        if kind == 'unsafe_block':
            return n('unsafe', body=many('body'))
        if kind in {'integer_literal', 'floating_literal', 'string_literal'}:
            return n('literal', value=python_ast.literal_eval(text('spelling')))
        if kind == 'boolean_literal':
            return n('literal', value=text('spelling') == 'true')
        if kind == 'null_literal':
            return n('literal', value=None)
        if kind == 'parenthesized_expression':
            return one('expression')
        if kind == 'binary_expression':
            return n('binary', operator=text('operator'), left=one('left'), right=one('right'))
        if kind == 'unary_expression':
            return n('unary', operator=text('operator'), operand=one('operand'))
        if kind == 'borrow_expression':
            return n('borrow', mode=text('mode').replace('&', '').strip(), operand=one('operand'))
        if kind in {'move_expression', 'dereference_expression'}:
            return n(kind.removesuffix('_expression'), operand=one('operand'))
        if kind == 'call_expression':
            return apply(one('callee'), many('arguments'))
        if kind == 'member_expression':
            receiver = one('receiver')
            if receiver['kind'] == 'name':
                return name(receiver['value'] + '.' + text('name'))
            return n('member', receiver=receiver, name=text('name'))
        if kind == 'index_expression':
            return n('index', target=one('target'), index=one('index'))
        if kind == 'slice_expression':
            return n('slice', target=one('target'), start=one('start'), end=one('end'))
        if kind == 'struct_literal':
            return n('struct_literal', type=one('type'), fields=many('fields'))
        if kind == 'struct_literal_field':
            return n('field_value', name=text('name'), value=one('value'))
        if kind == 'expression_statement':
            return one('expression')
        if kind == 'assignment_statement':
            return n('assignment', target=one('target'), value=one('value'))
        if kind == 'if_statement':
            branches = many('branches')
            otherwise = branches.pop()['body'] if branches and branches[-1]['kind'] == 'else' else None
            return n('if', branches=branches, otherwise=otherwise)
        if kind == 'if_branch':
            return n('if_branch', condition=one('condition'), body=one('body'))
        if kind == 'else_branch':
            return n('else', body=one('body'))
        if kind == 'while_statement':
            return n('while', condition=one('condition'), body=one('body'))
        if kind == 'for_statement':
            return n('for', initializer=one('initializer'), condition=one('condition'), update=one('update'), body=one('body'))
        if kind == 'try_statement':
            return n('try', body=one('body'), catches=many('catches'))
        if kind == 'catch_clause':
            return n('catch', name=text('name') or None, error_type=one('error_type'), body=one('body'))
        if kind in {'return_statement', 'raise_statement', 'print_statement'}:
            role = 'error' if kind == 'raise_statement' else 'value'
            return n(kind.removesuffix('_statement'), **{role: one(role)})
        if kind == 'rethrow_statement':
            return n('rethrow')
        if kind in {'match_statement', 'match_expression'}:
            return n('match', scrutinee=one('scrutinee'), arms=many('arms'))
        if kind == 'match_arm':
            return n('arm', pattern=one('pattern'), body=one('body'))
        if kind == 'variant_pattern':
            return n('pattern', name=text('name'), bindings=[None if isinstance(binding, dict) else binding
                                                            for binding in many('bindings')])
        if kind == 'wildcard_pattern':
            return n('pattern', name=None, bindings=[])
        if kind == 'binding_pattern':
            return text('name')
        if kind == 'formatted_text':
            value = python_ast.literal_eval('"' + text('spelling') + '"')
            return n('text', value=value.replace('{{', '{').replace('}}', '}'))
        if kind == 'formatted_string':
            return n('formatted', parts=many('parts'))
        raise AssertionError(f'Unnormalized bootstrap AST: {kind}')

    return [visit(root) for root in roots]


def first_difference(left, right, path='root'):
    if type(left) is not type(right):
        return path, left, right
    if isinstance(left, dict):
        if left.keys() != right.keys():
            return path, left, right
        for key in left:
            difference = first_difference(left[key], right[key], path + '.' + key)
            if difference:
                return difference
    elif isinstance(left, list):
        if len(left) != len(right):
            return path + '.length', len(left), len(right)
        for index, (a, b) in enumerate(zip(left, right)):
            difference = first_difference(a, b, f'{path}[{index}]')
            if difference:
                return difference
    elif left != right:
        return path, left, right
    return None
