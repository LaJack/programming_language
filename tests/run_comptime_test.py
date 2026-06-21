import sys
import os
import unittest

# Ensure the source-layout package is importable when running this script directly.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))


def main():
    loader = unittest.TestLoader()
    tests = loader.discover(start_dir=os.path.dirname(__file__), pattern="test_comptime_pass.py")
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(tests)
    if not result.wasSuccessful():
        sys.exit(1)
    print("run_comptime_test: OK")


if __name__ == "__main__":
    main()
