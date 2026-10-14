# Self-Hosted Type Checking

The bootstrap checker analyzes syntax without executing analyzed comptime code.
Its completion status describes this pass, not ownership safety or compilability.
`--check-types` returns 0 for complete checking, 1 for diagnosed errors, 2 for
invalid invocation, and 3 for required work that remains pending without errors.

## Pass Responsibilities

1. Load and resolve names with the existing project pipeline.
2. `collect_project_types` prepares checked file/node and symbol indexes, resolves
   declarations and signatures, and validates known stored layouts.
3. `check_project_bodies` visits bodies with an explicit checking context:
   enclosing signature, expected return, unsafe state, and generic/comptime regions.
   Intrinsic expression types and access categories are recorded once. Repeated
   requests reuse those results; contextual conversions and check failures live
   in separate tables.
4. `validate_typed_tables` verifies identities, source references, direct indexes,
   signature ranges, pending-check indexes, conversions, and issue causes.

The tables enforce declaration/body/check completion order and belong to one
project. A required result missing from a supported production is an invariant
failure. `has_result` and `has_signature` express genuinely optional queries;
lookup exceptions must not be converted into speculative generic deferrals.

Borrow and pointer qualifiers carry a `SyntaxAccess` value plus the exact mode
token span. Only the parser decodes spelling. Recovery produces invalid syntax
for invalid qualifiers; syntax validation checks the typed value against source.

## Rule Matrix

| Rule | Stage 0 contract | Bootstrap coverage | Regression |
| --- | --- | --- | --- |
| Declared names and types | Resolve to original declarations | Primitive/nominal identities; symbolic generic/computed types | `test_invalid_declared_type_takes_precedence`, `test_std_memory_generic_value_arguments` |
| Stored layouts | Reject infinite inline recursion | Concrete struct/union/array cycles; indirection stops traversal | `test_concrete_stored_layout_cycles_are_rejected`, `test_pointer_indirection_breaks_stored_layout_recursion` |
| Initializers | Validate destination compatibility and literal range | Variables, fields, constants; declared types survive errors | `test_field_and_constant_initializers_are_checked`, `test_integer_literal_range_boundaries` |
| Calls | Signature fixes arity and ownership/borrow contracts | Direct calls and concrete inherent methods; generic substitution remains partial | `test_concrete_call_arguments_and_arity_are_checked`, `test_concrete_methods_check_receiver_and_explicit_arguments` |
| Access | Immutable places cannot be written or mutably borrowed | Known names, fields, indexes, dereferences and receivers | `test_immutable_fields_and_constants_reject_assignment`, `test_mutable_methods_and_implicit_borrows_require_writable_places` |
| Numeric context | Intrinsic type is distinct from conversion | Literal range checks and separate initializer/call/assignment/return conversions | `test_contextual_conversions_preserve_intrinsic_literal_types` |
| Statements | Conditions are boolean; returns match signatures | Basic operators, assignments, conditions and conservative return paths | `test_conditions_returns_assignments_and_operators_are_checked`, `test_nonvoid_fallthrough_is_reported` |
| Qualifiers | Mode follows syntax, regardless of trivia | Typed modes with exact source spans | `test_qualifier_comments_and_whitespace_do_not_change_access` |
| Analysis effects | Checking does not execute source | Comptime execution remains pending | `test_comptime_body_is_not_executed` |
| Match expressions | Concrete matching is exhaustive and typed | Explicit `dependent_match` result until the remaining match rules are implemented | `test_match_return_has_an_explicit_pending_result` |

All named regressions are in `tests/test_bootstrap_typing.py`. The matrix describes
verified coverage rather than promising every expression form in these families.

Pending reasons retain their existing meanings: `computed_type`, `comptime_value`,
`generic_body`, `inferred_errors`, `interface_obligation`, `dependent_member`,
`dependent_match`, and `dependent_expression`. Each obligation names its source,
check category, and dependency. In particular, generic substitution, computed
members, comprehensive matches, inferred error sets, and nullable refinement
remain unfinished. Unknown internal state is not a legitimate pending reason.
The bootstrap graph currently passes name checking, not type checking: some
valid stage-0 programs still receive type errors from unfinished generic and
concrete rules. The focused rule matrix is the coverage contract for this
consolidation milestone.

## Development And Acceptance

```sh
.venv/bin/python -m tests.run_bootstrap_checks fast
.venv/bin/python -m tests.run_bootstrap_checks acceptance
.venv/bin/python -m unittest discover -s tests
npm --prefix vscode-jack test
npm --prefix vscode-jack run package:vsix
```

The fast suite runs artifact-registry and focused LLVM `-O0` typing cases.
Acceptance retains complete bootstrap corpus and interpreter/native coverage.
`tests/test_bootstrap_typing_acceptance.py` also exercises required-result
failures, phase misuse, foreign identities, table movement, and obligation
deduplication through the interpreter and both native backends.
Builds are reused across test classes within a process, for immutable inputs and
identical entry/root/stub/backend/optimization/debug/toolchain/extern settings.
Fixture files and interpreter snapshots remain isolated. Builds that assert
saved-artifact behavior own their directories and bypass the shared registry.
Failed builds propagate and are never cached; artifacts are deleted at exit.

Type dumps include `checking` counters and separate `conversion` records.
`test_intrinsic_expression_checks_are_once_per_node` asserts structure, not time.
Use `benchmarks/frontend/run.py --mode typing` for separate loading, indexing,
resolution, declaration, body, and validation measurements. Clocks live in the
benchmark harness, and build time is reported separately. Reports are written
under ignored `benchmarks/results/`; timings are informational.
