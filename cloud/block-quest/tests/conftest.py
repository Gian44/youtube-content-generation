import sys, pathlib
root = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
sys.path.insert(0, str(root.parent / "sleep-on-facts"))
sys.path.insert(0, str(root.parent / "common"))
