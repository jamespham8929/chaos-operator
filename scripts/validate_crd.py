#!/usr/bin/env python3
"""Validate the ChaosExperiment CRD schema and check the examples against it.

Two checks:

  1. The CRD's openAPIV3Schema is itself a well-formed JSON Schema.
  2. Every example ChaosExperiment manifest validates against that schema.

The second check is the one that catches drift. The operator reads fields like
spec.steadyStateHypothesis.query and spec.safety.guards[].threshold. If the CRD
renames or drops a field that an example (and the code) still uses, or an example
starts using a field the CRD does not declare, validation fails here instead of
at apply time in a real cluster.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

REPO = Path(__file__).resolve().parent.parent
CRD_PATH = REPO / "k8s" / "crds" / "chaosexperiment.yaml"
EXAMPLES_DIR = REPO / "examples"


def load_crd_schema(path: Path) -> dict:
    crd = yaml.safe_load(path.read_text())
    versions = crd["spec"]["versions"]
    version = next(v for v in versions if v.get("storage"))
    return version["schema"]["openAPIV3Schema"]


def iter_example_experiments(examples_dir: Path):
    for path in sorted(examples_dir.glob("*.yaml")):
        for doc in yaml.safe_load_all(path.read_text()):
            if isinstance(doc, dict) and doc.get("kind") == "ChaosExperiment":
                yield path, doc


def main() -> int:
    schema = load_crd_schema(CRD_PATH)
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)

    examples = list(iter_example_experiments(EXAMPLES_DIR))
    if not examples:
        print("no ChaosExperiment examples found to validate", file=sys.stderr)
        return 1

    failures = 0
    for path, doc in examples:
        name = doc.get("metadata", {}).get("name")
        errors = sorted(validator.iter_errors(doc), key=lambda e: list(e.absolute_path))
        if errors:
            failures += 1
            print(f"FAIL {path.name} ({name})")
            for err in errors:
                loc = "/".join(str(p) for p in err.absolute_path) or "<root>"
                print(f"  - {loc}: {err.message}")
        else:
            print(f"OK   {path.name} ({name})")

    if failures:
        print(f"\n{failures} example(s) failed CRD schema validation", file=sys.stderr)
        return 1
    print(f"\nall {len(examples)} example(s) conform to the CRD schema")
    return 0


if __name__ == "__main__":
    sys.exit(main())
