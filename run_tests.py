"""Stdlib test runner (no pytest dependency): python3 run_tests.py"""
import sys

sys.path.insert(0, "tests")
sys.path.insert(0, ".")
import test_stage1 as t1  # noqa: E402
import test_stage234 as t2  # noqa: E402
import test_stage6 as t3  # noqa: E402
import test_fix1 as t4  # noqa: E402
import test_fix1b as t5  # noqa: E402
import test_fix1c as t6  # noqa: E402
import test_fix2 as t7  # noqa: E402
import test_fix3 as t8  # noqa: E402
import test_partA as t9  # noqa: E402
import test_partB as t10  # noqa: E402
import test_reddit_potd_fixture as t11  # noqa: E402

if __name__ == "__main__":
    total = 0
    for mod in (t1, t2, t3, t4, t5, t6, t7, t8, t9, t10, t11):
        fns = [getattr(mod, n) for n in dir(mod) if n.startswith("test_")]
        for f in fns:
            f()
            print("PASS", f"{mod.__name__}.{f.__name__}")
            total += 1
    print(f"{total} tests passed")
