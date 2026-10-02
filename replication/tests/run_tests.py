from pathlib import Path
import sys
import unittest
root = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(root / "src"), str(root / "src/opindx_replication/core")]
suite = unittest.defaultTestLoader.discover(str(root / "tests"))
raise SystemExit(not unittest.TextTestRunner(verbosity=1).run(suite).wasSuccessful())
