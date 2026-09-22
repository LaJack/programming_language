"""Independent stage-0 projection of source declarations and lexical references.

This module reads original source through the Python parser. It never consumes
bootstrap project bindings or runs comptime evaluation.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from pathlib import Path

from jack import ast_nodes as ast
from jack.module_loader import load_source_graph
from jack.parser import Lexer, Parser, parse
from jack.source_model import SourceSpan, TypeReference


@dataclass(frozen=True)
class Target:
    path: str | None
    start: int
    end: int
    kind: str
    name: str
    owner: tuple | None = None

    @property
    def identity(self):
        return (self.path, self.start, self.end, self.kind, self.name, self.owner)


@dataclass(frozen=True)
class Reference:
    path: str
    start: int
    end: int
    role: str
    status: str
    target: Target | str | None


class Source:
    def __init__(self, path: Path):
        self.path = str(path.resolve())
        self.text = path.read_text()
        self.tokens = Lexer(self.text, source_path=path).tokenize()
        self.nodes = parse(self.text, source_path=path)
        offsets = [0]
        for char in self.text:
            offsets.append(offsets[-1] + len(char.encode("utf-8")))
        self.byte_offsets = offsets
        self.span_bases = {}
        self.identifier_tokens = [
            (token.value, token.span.start_offset, token.span.end_offset)
            for token in self.tokens if token.kind == "IDENT"
        ]
        for node in self.nodes:
            self._index_spans(node, 0)

    def _index_spans(self, value, base):
        if isinstance(value, (list, tuple)):
            for part in value:
                self._index_spans(part, base)
            return
        if not is_dataclass(value):
            return
        for field in fields(value):
            part = getattr(value, field.name)
            if isinstance(part, SourceSpan):
                self.span_bases[id(part)] = base
            elif isinstance(value, ast.FormattedStringExpression):
                continue
            else:
                self._index_spans(part, base)
        if isinstance(value, ast.FormattedStringExpression):
            self._index_formatted(value, base)

    def _index_formatted(self, value, base):
        # Stage 0 reparses each embedded expression from a substring. The
        # FSTRING token and its delimiters recover the absolute source base.
        content_start = base + value.span.start_offset + 2
        raw = self.text[content_start:base + value.span.end_offset - 1]
        parts = iter(part for part in value.parts if is_dataclass(part))
        token = next((item for item in self.tokens if item.kind == "FSTRING"), None)
        parser = Parser(self.tokens)
        index = 0
        while index < len(raw):
            if raw[index] == "\\":
                index += 2
            elif raw[index:index + 2] in {"{{", "}}"}:
                index += 2
            elif raw[index] == "{":
                end = parser._formatted_expression_end(raw, index + 1, token)
                leading = len(raw[index + 1:end]) - len(raw[index + 1:end].lstrip())
                expression_base = content_start + index + 1 + leading
                expression_source = raw[index + 1:end].strip()
                for item in Lexer(expression_source).tokenize():
                    if item.kind == "IDENT":
                        self.identifier_tokens.append((
                            item.value,
                            expression_base + item.span.start_offset,
                            expression_base + item.span.end_offset,
                        ))
                self._index_spans(next(parts), expression_base)
                index = end + 1
            else:
                index += 1

    def byte_range(self, span):
        base = self.span_bases.get(id(span), 0)
        return (self.byte_offsets[base + span.start_offset],
                self.byte_offsets[base + span.end_offset])

    def name_range(self, span, name, after=None, last=False):
        if span is None and after is None:
            raise AssertionError(f"Missing source span for {name!r} in {self.path}")
        base = 0 if span is None else self.span_bases.get(id(span), 0)
        start = after if span is None else (
            base + span.start_offset if after is None
            else max(base + span.start_offset, after))
        end = len(self.text) if span is None else base + span.end_offset
        candidates = [(token_start, token_end)
                      for spelling, token_start, token_end in self.identifier_tokens
                      if spelling == name and start <= token_start
                      and token_end <= end]
        if not candidates:
            raise AssertionError(f"No token {name!r} in {self.path}:{span}")
        token_start, token_end = candidates[-1 if last else 0]
        return self.byte_offsets[token_start], self.byte_offsets[token_end]

    def target(self, node, name, kind, owner=None, after=None):
        start, end = self.name_range(node.span, name, after=after)
        return Target(self.path, start, end, kind, name,
                      None if owner is None else owner.identity)


BUILTIN_TYPES = {
    "void", "bool", "b8", "i8", "i16", "i32", "i64", "u8", "u16", "u32",
    "u64", "isize", "usize", "f32", "f64", "str", "type", "c_char",
    "c_void", "MaybeUninit", "Allocation", "UnionVariant", "UnionField",
}
BUILTIN_FUNCTIONS = {
    "Union", "variant", "field", "move_field", "len", "sizeof", "alignof",
    "raw", "initialized_slice",
}


def builtins():
    symbols = {}
    for name in BUILTIN_TYPES:
        symbols[name] = Target(None, 0, 0, "builtin_type", name)
    for name in BUILTIN_FUNCTIONS:
        symbols[name] = Target(None, 0, 0, "builtin_function", name)
    symbols["Copyable"] = Target(None, 0, 0, "interface_type", "Copyable")
    return symbols


class Scope:
    def __init__(self, parent=None):
        self.parent = parent
        self.names = {}
        self.generic_owner = None if parent is None else parent.generic_owner

    def bind(self, symbol):
        self.names[symbol.name] = symbol

    def lookup(self, name):
        if name in self.names:
            return self.names[name]
        return None if self.parent is None else self.parent.lookup(name)


class StageZeroBindings:
    def __init__(self, entry, roots=(), stubs=None):
        graph = load_source_graph(Path(entry), import_overrides=stubs or {},
                                  search_roots=[Path(root) for root in roots])
        self.sources = {name: Source(module.path)
                        for name, module in graph.modules.items()}
        self.graph = graph
        self.declarations = {}
        self.module_symbols = {}
        self.members = {}
        self.references = []
        self._builtins = builtins()
        self._index_declarations()
        self._visit_modules()

    def _declare(self, source, node, name, kind, owner=None, after=None):
        symbol = source.target(node, name, kind, owner=owner, after=after)
        self.declarations[(symbol.path, symbol.start, symbol.end)] = symbol
        return symbol

    def _index_declarations(self):
        for module_name, source in self.sources.items():
            exports = {}
            self.module_symbols[module_name] = exports
            for node in source.nodes:
                if isinstance(node, ast.FunctionDeclaration):
                    after = node.return_type.span.end_offset if node.return_type.span else None
                    exports[node.name] = self._declare(source, node, node.name, "function",
                                                       after=after)
                elif isinstance(node, (ast.TypeDeclaration, ast.EnumDeclaration,
                                       ast.ViewDeclaration, ast.InterfaceDeclaration)):
                    kind = ("structure" if isinstance(node, ast.TypeDeclaration) else
                            "sum_type" if isinstance(node, ast.EnumDeclaration) else
                            "view_type" if isinstance(node, ast.ViewDeclaration) else
                            "interface_type")
                    owner = self._declare(source, node, node.name, kind)
                    exports[node.name] = owner
                    members = self.members[owner.identity] = {}
                    for field in getattr(node, "fields", ()):
                        member_kind = ("view_field" if isinstance(field, ast.ViewField)
                                       else "field")
                        members[field.name] = self._declare(
                            source, field, field.name, member_kind, owner=owner,
                            after=field.type.span.end_offset if field.type.span else None)
                    for variant in getattr(node, "variants", ()):
                        members[variant.name] = self._declare(
                            source, variant, variant.name, "variant", owner=owner)
                    for method in getattr(node, "methods", ()):
                        member_kind = ("interface_requirement"
                                       if isinstance(node, ast.InterfaceDeclaration)
                                       else "inherent_method")
                        after = (method.return_type.span.end_offset
                                 if method.return_type.span else None)
                        members[method.name] = self._declare(
                            source, method, method.name, member_kind,
                            owner=owner, after=after)
                elif isinstance(node, ast.VariableDeclaration):
                    kind = ("computed_type" if node.comptime and node.type.name == "type"
                            else "constant" if node.constant else "global")
                    after = node.type.span.end_offset if node.type.span else None
                    exports[node.name] = self._declare(source, node, node.name, kind,
                                                       after=after)

    def _visit_modules(self):
        for module_name, source in self.sources.items():
            scope = Scope()
            scope.names.update(self._builtins)
            scope.names.update(self.module_symbols[module_name])
            own_names = set(self.module_symbols[module_name])
            aliases = {}
            for node in source.nodes:
                if not isinstance(node, ast.ImportDeclaration):
                    continue
                imported = self.module_symbols.get(node.module_name, {})
                if node.alias:
                    aliases[node.alias] = node.module_name
                    scope.bind(source.target(node, node.alias, "module_alias"))
                elif node.symbols:
                    for name in node.symbols:
                        if name in imported:
                            scope.bind(imported[name])
                else:
                    for name, symbol in imported.items():
                        if name not in own_names:
                            scope.names[name] = symbol
            for node in source.nodes:
                self._visit(source, node, scope, aliases)

    def _record(self, source, span, name, role, symbol=None, reason=None):
        start, end = source.name_range(span, name)
        if symbol is not None:
            status, target = "resolved", symbol
        elif reason is not None:
            status, target = "deferred", reason
        else:
            status, target = "invalid", None
        self.references.append(Reference(source.path, start, end, role, status, target))
        return symbol

    def _type(self, source, value, scope, aliases, expression=False):
        if value is None:
            return
        if not isinstance(value, TypeReference):
            self._visit(source, value, scope, aliases)
            return
        if value.span is None or value.name == "self":
            return
        segments = value.name.split(".")
        first = segments[0]
        symbol = scope.lookup(first)
        self._record(source, value.span, first,
                     ("call" if value.arguments else "read") if expression
                     else "type_use", symbol)
        if len(segments) > 1:
            for name in segments[1:]:
                if first in aliases:
                    symbol = self.module_symbols[aliases[first]].get(name)
                    self._record(source, value.span, name, "member", symbol)
                elif symbol is not None and symbol.identity in self.members:
                    symbol = self.members[symbol.identity].get(name)
                    self._record(source, value.span, name, "member", symbol)
                else:
                    self._record(source, value.span, name, "member",
                                 reason="receiver_type")
        for argument in value.arguments:
            # Bootstrap parses a bare primitive generic argument as an expression.
            primitive_argument = (isinstance(argument, TypeReference)
                                  and argument.name in BUILTIN_TYPES
                                  and not argument.arguments
                                  and argument.array_size is None
                                  and not argument.is_slice
                                  and argument.borrow is None
                                  and argument.pointer_mode is None)
            self._type(source, argument, scope, aliases,
                       expression=expression or primitive_argument)
        if value.array_size is not None:
            self._visit(source, value.array_size, scope, aliases)

    def _visit(self, source, node, scope, aliases, role="read"):
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, (list, tuple)):
            for part in node:
                self._visit(source, part, scope, aliases, role)
            return
        if isinstance(node, TypeReference):
            self._type(source, node, scope, aliases)
            return
        if isinstance(node, ast.TypeExpression):
            self._type(source, node.type_ref, scope, aliases, expression=True)
            return
        if isinstance(node, (ast.ModuleDeclaration, ast.ImportDeclaration)):
            return
        if isinstance(node, (ast.TypeDeclaration, ast.EnumDeclaration,
                             ast.ViewDeclaration, ast.InterfaceDeclaration)):
            owner = self.module_symbols[next(
                name for name, module in self.sources.items() if module is source)][node.name]
            member_scope = Scope(scope)
            member_scope.generic_owner = owner
            member_scope.bind(Target(owner.path, owner.start, owner.end,
                                     "self_type", "Self", owner.identity))
            for member in self.members[owner.identity].values():
                if member.kind in {"inherent_method", "interface_requirement"}:
                    member_scope.bind(member)
            self._visit(source, getattr(node, "parameters", ()),
                        member_scope, aliases, "generic_parameter")
            self._visit(source, getattr(node, "fields", ()), member_scope, aliases)
            self._visit(source, getattr(node, "variants", ()), member_scope, aliases)
            for method in getattr(node, "methods", ()):
                self._visit(source, method, member_scope, aliases)
            return
        if isinstance(node, ast.ImplementationDeclaration):
            start, end = source.byte_range(node.span)
            owner = Target(source.path, start, end, "implementation",
                           "<implementation>")
            implementation_scope = Scope(scope)
            implementation_scope.generic_owner = owner
            self._visit(source, node.parameters, implementation_scope,
                        aliases, "generic_parameter")
            if node.type_name:
                self._record(source, node.span, node.type_name.split(".")[0],
                             "type_use", implementation_scope.lookup(
                                 node.type_name.split(".")[0]))
            self._type(source, node.interface, implementation_scope, aliases)
            for method in node.methods:
                self._visit(source, method, implementation_scope, aliases)
            return
        if isinstance(node, ast.ViewField):
            self._type(source, node.type, scope, aliases)
            return
        if isinstance(node, ast.EnumVariant):
            self._visit(source, node.parameters, scope, aliases, "parameter")
            return
        if isinstance(node, ast.StructLiteralExpression):
            owner = scope.lookup(node.type_ref.name)
            self._type(source, node.type_ref, scope, aliases)
            for field in node.fields:
                target = (self.members.get(owner.identity, {}).get(field.name)
                          if owner is not None else None)
                candidates = [
                    token for token in source.tokens
                    if token.kind == "IDENT" and token.value == field.name
                    and node.span.start_offset <= token.span.start_offset
                    and token.span.end_offset <= field.expr.span.start_offset
                ]
                if not candidates:
                    raise AssertionError(f"Missing struct field {field.name!r}")
                self._record(source, candidates[-1].span, field.name, "member", target,
                             None if target else "receiver_type")
                self._visit(source, field.expr, scope, aliases)
            return
        if isinstance(node, ast.EnumVariantExpression):
            owner = scope.lookup(node.type_ref.name)
            self._type(source, node.type_ref, scope, aliases)
            target = (self.members.get(owner.identity, {}).get(node.variant_name)
                      if owner is not None else None)
            self._record(source, node.span, node.variant_name, "member", target,
                         None if target else "receiver_type")
            self._visit(source, node.arguments, scope, aliases)
            return
        if isinstance(node, ast.VariableExpression):
            parts = node.name.split(".")
            symbol = scope.lookup(parts[0])
            self._record(source, node.span, parts[0],
                         role if len(parts) == 1 else "read", symbol)
            for name in parts[1:]:
                if parts[0] in aliases:
                    symbol = self.module_symbols[aliases[parts[0]]].get(name)
                    self._record(source, node.span, name, "member", symbol)
                elif symbol is not None and symbol.identity in self.members:
                    symbol = self.members[symbol.identity].get(name)
                    self._record(source, node.span, name, "member", symbol)
                else:
                    reason = ("generic_type" if symbol is not None and
                              symbol.kind in {"type_parameter", "self_type"} else
                              "computed_type" if symbol is not None and
                              symbol.kind == "computed_type" else "receiver_type")
                    symbol = None
                    self._record(source, node.span, name, "member", reason=reason)
            return symbol
        if isinstance(node, ast.MemberExpression):
            owner = self._visit(source, node.target, scope, aliases)
            if isinstance(node.target, ast.VariableExpression) and node.target.name in aliases:
                target = self.module_symbols[aliases[node.target.name]].get(node.member)
                self._record(source, node.member_span or node.span, node.member,
                             "member", target)
                return target
            if owner is not None and owner.identity in self.members:
                target = self.members[owner.identity].get(node.member)
                self._record(source, node.member_span or node.span, node.member,
                             "member", target)
                return target
            reason = ("generic_type" if owner is not None and owner.kind in
                      {"type_parameter", "self_type"} else
                      "computed_type" if owner is not None and
                      owner.kind == "computed_type" else "receiver_type")
            self._record(source, node.member_span or node.span, node.member,
                         "member", reason=reason)
            return None
        if isinstance(node, ast.FunctionCall):
            self._visit(source, node.callee, scope, aliases, "call")
            self._visit(source, node.parameters, scope, aliases)
            return
        if isinstance(node, ast.Assignment):
            self._visit(source, node.target, scope, aliases, "write")
            self._visit(source, node.expr, scope, aliases)
            return
        if isinstance(node, ast.VariableDeclaration):
            if not (node.comptime and node.type.name == "type"
                    and role not in {"parameter", "generic_parameter"}):
                self._type(source, node.type, scope, aliases)
            self._visit(source, node.constraints, scope, aliases)
            self._visit(source, node.expr, scope, aliases)
            self._visit(source, node.constructor_args, scope, aliases)
            if scope.lookup(node.name) is None or node.name not in scope.names:
                kind = ("type_parameter" if node.comptime and node.type.name == "type"
                        else "parameter" if role in {"parameter", "generic_parameter"}
                        else "local")
                after = node.type.span.end_offset if node.type.span else None
                if node.name == "self":
                    after = None
                scope.bind(self._declare(
                    source, node, node.name, kind,
                    owner=scope.generic_owner if role == "generic_parameter" else None,
                    after=after))
            return
        if isinstance(node, ast.FunctionDeclaration):
            fn_scope = Scope(scope)
            fn_scope.generic_owner = None
            if node.self_parameter:
                self._visit(source, node.self_parameter, fn_scope, aliases, "parameter")
            for parameter in node.parameters:
                self._visit(source, parameter, fn_scope, aliases, "parameter")
            self._type(source, node.return_type, fn_scope, aliases)
            self._visit(source, node.raises, fn_scope, aliases)
            self._visit(source, node.body, fn_scope, aliases)
            return
        if isinstance(node, ast.Block):
            self._visit(source, node.body, Scope(scope), aliases)
            return
        if isinstance(node, ast.If):
            for branch in node.branches:
                self._visit(source, branch.condition, scope, aliases)
                self._visit(source, branch.body, Scope(scope), aliases)
            self._visit(source, node.else_body, Scope(scope), aliases)
            return
        if isinstance(node, ast.For):
            loop_scope = Scope(scope)
            self._visit(source, node.initializer, loop_scope, aliases)
            self._visit(source, node.condition, loop_scope, aliases)
            self._visit(source, node.update, loop_scope, aliases)
            self._visit(source, node.body, Scope(loop_scope), aliases)
            return
        if isinstance(node, ast.While):
            self._visit(source, node.condition, scope, aliases)
            self._visit(source, node.body, Scope(scope), aliases)
            return
        if isinstance(node, ast.Try):
            self._visit(source, node.body, Scope(scope), aliases)
            for clause in node.catches:
                self._type(source, clause.error_type, scope, aliases)
                catch_scope = Scope(scope)
                if clause.name:
                    catch_scope.bind(self._declare(
                        source, clause, clause.name, "catch_binding",
                        after=clause.error_type.span.end_offset))
                self._visit(source, clause.body, catch_scope, aliases)
            return
        if isinstance(node, ast.Match):
            self._visit(source, node.scrutinee, scope, aliases)
            for arm in node.arms:
                arm_scope = Scope(scope)
                if arm.variant_name:
                    self._record(source, arm.span, arm.variant_name, "pattern",
                                 reason="pattern_type")
                binding_after = arm.span.start_offset
                if arm.variant_name:
                    variant_token = next(token for token in source.tokens
                                         if token.kind == "IDENT"
                                         and token.value == arm.variant_name
                                         and arm.span.start_offset <=
                                         token.span.start_offset < arm.span.end_offset)
                    binding_after = variant_token.span.end_offset
                for binding in arm.bindings:
                    if binding.name:
                        arm_scope.bind(self._declare(source, binding, binding.name,
                                                     "match_binding",
                                                     after=binding_after))
                self._visit(source, arm.expr, arm_scope, aliases)
                self._visit(source, arm.body, arm_scope, aliases)
            return
        if is_dataclass(node):
            for field in fields(node):
                if field.name in {"span", "module_name", "source_name", "imports",
                                  "qualified_imports", "public", "comptime",
                                  "name", "abi", "synthetic", "interface_name"}:
                    continue
                self._visit(source, getattr(node, field.name), scope, aliases)
            return
        raise AssertionError(f"Unhandled stage-0 node: {type(node).__name__}")


def bootstrap_projection(dump):
    rows = [line.split("\t") for line in dump.splitlines()]
    symbols = {}
    for row in rows:
        if row[0] != "symbol":
            continue
        _, identifier, name, kind, _, owner_id, path, start, end, _ = row
        owner = symbols[owner_id].identity if owner_id != "-" else None
        symbols[identifier] = Target(None if path == "-" else path, int(start),
                                     int(end), kind, name, owner)
    result = []
    for row in rows:
        if row[0] != "reference":
            continue
        _, _, path, start, end, _, role, status, value = row
        target = symbols[value] if status == "resolved" else (
            value if status == "deferred" else None)
        result.append(Reference(path, int(start), int(end), role, status, target))
    return symbols, result
