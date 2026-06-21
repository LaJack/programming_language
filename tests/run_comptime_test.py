import sys
import os
import unittest

# Ensure project root is on sys.path so `src` package is importable when
# running this script directly from the tests directory.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


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
