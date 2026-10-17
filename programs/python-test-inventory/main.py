"""Inventory unittest-style test methods without importing or executing tests."""

import ast
import json
from pathlib import Path
import sys

# Inspect syntax without importing or executing the requested test modules.
args = json.load(sys.stdin)
root = Path(args["root"]).resolve()
files = []
for relative in sorted(args["files"]):
    path = root / relative
    tree = ast.parse(path.read_text(), filename=relative)
    classes = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            methods = sorted(child.name for child in node.body
                             if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                             and child.name.startswith("test_"))
            if methods:
                classes.append({"class": node.name, "tests": len(methods)})
    functions = sum(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name.startswith("test_") for node in tree.body)
    files.append({"file": relative, "classes": sorted(classes, key=lambda row: row["class"]),
                  "test_functions": functions,
                  "tests": functions + sum(row["tests"] for row in classes)})
json.dump({"files_scanned": len(files), "tests": sum(row["tests"] for row in files),
           "files": files}, sys.stdout, sort_keys=True, separators=(",", ":"))
