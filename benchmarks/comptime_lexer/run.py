#!/usr/bin/env python3

import argparse
import cProfile
import pstats
import re
import time
from pathlib import Path

from jack.compile_time_pass import (
    CompileTimePass,
    ComptimeEffects,
    ComptimeStructValue,
    apply_compile_time_pass,
)
from jack.comptime_externs import default_comptime_externs
from jack.hir_lowering_pass import lower_to_hir
from jack.module_loader import load_source_file


ROOT = Path(__file__).resolve().parents[2]
ENTRY = ROOT / 'selfhost' / 'bootstrap' / 'main.jack'
SELFHOST = ROOT / 'selfhost'
RESULTS = ROOT / 'benchmarks' / 'results' / 'comptime-lexer'


def timed(function):
    started = time.perf_counter()
    value = function()
    return value, time.perf_counter() - started


def compile_once():
    generator_stats = {}
    original = CompileTimePass._apply_variable_declaration

    def capture_generator_result(self, declaration, scope):
        result = original(self, declaration, scope)
        if declaration.name.endswith('$compiled_lexer'):
            literal = scope.get(declaration.name)
            if literal is not None and isinstance(literal.value, ComptimeStructValue):
                generator_stats['nfa_states'] = literal.value.fields['nfa_states'].value
                generator_stats['nfa_edges'] = literal.value.fields['nfa_edges'].value
        return result

    ast, loading = timed(lambda: load_source_file(ENTRY, search_roots=[SELFHOST]))
    effects = ComptimeEffects()
    CompileTimePass._apply_variable_declaration = capture_generator_result
    try:
        runtime_ast, comptime = timed(lambda: apply_compile_time_pass(
            ast,
            print_handler=None,
            externs=default_comptime_externs(),
            effects=effects,
        ))
    finally:
        CompileTimePass._apply_variable_declaration = original
    program, hir = timed(lambda: lower_to_hir(runtime_ast))
    total = loading + comptime + hir

    table_type = next(
        declaration.symbol.type_ref.name
        for declaration in program.declarations
        if getattr(getattr(declaration, 'symbol', None), 'source_name', None)
        == 'lexer_tables'
    )
    match = re.search(r'\$States\$(\d+)\$Rules\$(\d+)$', table_type)
    if match is None:
        raise RuntimeError(f'Cannot read generated table dimensions from {table_type}')
    states, rules = map(int, match.groups())
    transitions = states * 256
    table_bytes = transitions * 2 + states * 2 + rules * 4

    print(f'module loading: {loading:.3f}s')
    print(f'comptime execution: {comptime:.3f}s')
    print(f'HIR lowering: {hir:.3f}s')
    print(f'total: {total:.3f}s')
    print(
        f'NFA states: {generator_stats["nfa_states"]}; '
        f'NFA edges: {generator_stats["nfa_edges"]}'
    )
    print(f'DFA states: {states}; rules: {rules}; transitions: {transitions}')
    print(f'frozen table size: {table_bytes} bytes')
    print(f'comptime dependencies: {len(effects.dependencies)}')


def profile_once() -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    profile_path = RESULTS / 'cold-generation.prof'
    profiler = cProfile.Profile()
    profiler.enable()
    compile_once()
    profiler.disable()
    profiler.dump_stats(profile_path)

    stats = pstats.Stats(profiler)
    clone_time = sum(
        data[3]
        for key, data in stats.stats.items()
        if key[2] == 'deepcopy' and key[0].endswith('/copy.py')
    )
    print(f'Python calls: {stats.total_calls}')
    print(f'generic deepcopy cumulative time: {clone_time:.3f}s')
    print(f'profile: {profile_path.relative_to(ROOT)}')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--profile', action='store_true', help='also write a cProfile report'
    )
    arguments = parser.parse_args()
    if arguments.profile:
        profile_once()
    else:
        compile_once()


if __name__ == '__main__':
    main()
