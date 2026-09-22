"""Source-identifier coverage for the immutable bootstrap syntax model."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
import re
import subprocess

from jack.parser import Lexer, Parser


# Every SyntaxData variant is listed, including variants without source text.
# Values classify SourceSpan fields, not child nodes.
FIELDS = {
    "invalid_declaration": {},
    "invalid_statement": {},
    "invalid_type": {},
    "invalid_pattern": {},
    "invalid_expression": {},
    "module_declaration": {"path": "path"},
    "import_declaration": {"path": "path", "alias": "declaration"},
    "import_path": {"name": "path"},
    "import_symbol": {"name": "reference"},
    "struct_declaration": {"name": "declaration", "abi": "literal"},
    "union_declaration": {"name": "declaration", "abi": "literal"},
    "interface_declaration": {"name": "declaration", "abi": "literal"},
    "implementation_declaration": {},
    "implementation_use": {"name": "reference"},
    "view_declaration": {"name": "declaration", "abi": "literal"},
    "view_field": {"name": "declaration", "mode": "syntax"},
    "extern_type_declaration": {"name": "declaration", "abi": "literal"},
    "function_declaration": {"name": "declaration", "abi": "literal"},
    "parameter": {"name": "declaration", "abi": "literal"},
    "field_declaration": {"name": "declaration", "abi": "literal"},
    "variable_declaration": {"name": "declaration", "abi": "literal"},
    "constant_declaration": {"name": "declaration", "abi": "literal"},
    "comptime_type_declaration": {"name": "declaration", "abi": "literal"},
    "union_variant": {"name": "declaration"},
    "block_statement": {},
    "unsafe_block": {},
    "comptime_statement": {},
    "expression_statement": {},
    "assignment_statement": {},
    "if_statement": {},
    "if_branch": {},
    "else_branch": {},
    "while_statement": {},
    "for_statement": {},
    "try_statement": {},
    "catch_clause": {"name": "declaration"},
    "return_statement": {},
    "raise_statement": {},
    "rethrow_statement": {},
    "print_statement": {},
    "match_statement": {},
    "match_expression": {},
    "match_arm": {},
    "variant_pattern": {"name": "reference"},
    "wildcard_pattern": {},
    "binding_pattern": {"name": "declaration"},
    "named_type": {"name": "reference"},
    "generic_type": {"name": "reference"},
    "array_type": {},
    "slice_type": {},
    "borrow_type": {"mode": "syntax"},
    "raw_pointer_type": {"mode": "syntax"},
    "nullable_pointer_type": {"mode": "syntax"},
    "type_argument": {},
    "name_expression": {"name": "reference"},
    "integer_literal": {"spelling": "literal"},
    "floating_literal": {"spelling": "literal"},
    "string_literal": {"spelling": "literal"},
    "formatted_string": {},
    "formatted_text": {"spelling": "literal"},
    "boolean_literal": {"spelling": "syntax"},
    "null_literal": {},
    "unary_expression": {"operator": "syntax"},
    "binary_expression": {"operator": "syntax"},
    "borrow_expression": {"mode": "syntax"},
    "move_expression": {},
    "dereference_expression": {},
    "call_expression": {},
    "member_expression": {"name": "reference"},
    "index_expression": {},
    "slice_expression": {},
    "struct_literal": {},
    "struct_literal_field": {"name": "reference"},
    "union_variant_expression": {"name": "reference"},
    "parenthesized_expression": {},
    "absent": {},
}

IGNORED_IDENTIFIERS = Parser.KEYWORDS | {"_", "type"}


def assert_complete_classification(syntax_path: Path):
    source = syntax_path.read_text()
    kind_block = source.split("pub union SyntaxKind {", 1)[1].split("}", 1)[0]
    kinds = set(re.findall(r"^\s*(\w+);", kind_block, re.MULTILINE))
    data_block = source.split("pub union SyntaxData {", 1)[1].split("}", 1)[0]
    variants = {}
    for line in data_block.splitlines():
        match = re.fullmatch(r"\s*(\w+)(?:\((.*)\))?;", line)
        if match:
            signature = match.group(2) or ""
            variants[match.group(1)] = set(
                re.findall(r"\bSourceSpan\s+(\w+)", signature))
            if "DeclarationModifiers modifiers" in signature:
                variants[match.group(1)].add("abi")
    assert kinds == variants.keys(), (kinds - variants.keys(),
                                      variants.keys() - kinds)
    assert kinds == FIELDS.keys(), (kinds - FIELDS.keys(), FIELDS.keys() - kinds)
    for kind, fields in variants.items():
        assert fields == FIELDS[kind].keys(), (
            kind, fields - FIELDS[kind].keys(), FIELDS[kind].keys() - fields)


class TokenSource:
    """Lexical source projection for recovered files that strict parse rejects."""

    def __init__(self, path: Path):
        self.path = str(path.resolve())
        self.text = path.read_text()
        self.tokens = Lexer(self.text, source_path=path).tokenize()
        offsets = [0]
        for char in self.text:
            offsets.append(offsets[-1] + len(char.encode("utf-8")))
        self.offsets = offsets

    def byte_range(self, span):
        return self.offsets[span.start_offset], self.offsets[span.end_offset]


def coverage_for_source(executable: Path, source, symbols, references,
                        *, recovered=False):
    path = source.path
    result = subprocess.run(
        [str(executable), "--dump", "syntax", path],
        capture_output=True, text=True, timeout=45,
    )
    assert result.returncode == (1 if recovered else 0), (
        path, result.returncode, result.stderr)

    declarations = {
        (symbol.start, symbol.end) for symbol in symbols.values()
        if symbol.path == path and symbol.kind not in {"self_type", "implementation"}
    }
    source_references = [reference for reference in references
                         if reference.path == path and reference.role != "declaration"]
    reference_spans = {(reference.start, reference.end)
                       for reference in source_references}
    duplicate_references = [
        key for key, count in Counter(
            (reference.start, reference.end, reference.role)
            for reference in source_references).items() if count != 1
    ]
    assert not duplicate_references, (path, duplicate_references[:10])

    token_spans = {}
    for token in source.tokens:
        if token.kind == "IDENT":
            token_spans[source.byte_range(token.span)] = token.value

    nodes = {}
    invalid_regions = []
    field_spans = set()
    path_spans = set()
    problems = []
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if parts[0] == "node":
            nodes[parts[1]] = parts[2]
            if parts[2].startswith("invalid_"):
                invalid_regions.append((int(parts[3]), int(parts[4])))
        elif parts[0] == "field":
            _, node_id, field, start, end = parts
            kind = nodes[node_id]
            category = FIELDS[kind][field]
            start, end = int(start), int(end)
            if start == end:
                continue
            for span, spelling in token_spans.items():
                if not (start <= span[0] and span[1] <= end):
                    continue
                field_spans.add(span)
                if category == "path":
                    path_spans.add(span)
                elif category == "declaration" and span not in declarations:
                    problems.append((kind, field, spelling, span, "missing declaration"))
                elif category == "reference" and span not in reference_spans:
                    problems.append((kind, field, spelling, span, "missing reference"))

    if recovered:
        assert invalid_regions, (path, "missing invalid syntax nodes")
    def in_recovered_region(span):
        return any(start <= span[0] and span[1] <= end
                   for start, end in invalid_regions)

    uncovered = {
        (span, spelling) for span, spelling in token_spans.items()
        if spelling not in IGNORED_IDENTIFIERS
        and span not in declarations
        and span not in reference_spans
        and span not in path_spans
        and not in_recovered_region(span)
    }
    unexpected = {span for span in reference_spans - field_spans
                  if not in_recovered_region(span)}
    assert not problems, (path, problems[:20])
    assert not uncovered, (path, sorted(uncovered)[:20])
    assert not unexpected, (path, sorted(unexpected)[:20])
    return len(nodes), len(token_spans), len(reference_spans)
