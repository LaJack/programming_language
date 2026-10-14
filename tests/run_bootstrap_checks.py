"""Named fast and complete bootstrap suites sharing process-local artifacts."""

import argparse
import sys
import unittest


FAST = (
    "tests.test_bootstrap_artifacts",
    "tests.test_bootstrap_typing",
)
ACCEPTANCE = (
    *FAST,
    "tests.test_hash_map_library",
    "tests.test_bootstrap_typing_acceptance",
    "tests.test_bootstrap_analysis",
    "tests.test_bootstrap_frontend",
    "tests.test_bootstrap_modules",
    "tests.test_bootstrap_project",
    "tests.test_bootstrap_lexer",
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("suite", choices=("fast", "acceptance"))
    args = parser.parse_args()
    names = FAST if args.suite == "fast" else ACCEPTANCE
    suite = unittest.defaultTestLoader.loadTestsFromNames(names)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
