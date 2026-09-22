#!/usr/bin/env python3
"""Check what a real n8n instance made of the exports in automation/n8n.

Reads two files produced by automation/test/n8n-import-check.sh against a
throwaway container, and the exports themselves:

  --registry  the node types that n8n build registers, from
              automation/test/n8n-node-registry.js
  --listing   the output of `n8n list:workflow` after the import, one
              `id|name` line per workflow the instance now holds

It fails when an export names a node type the build does not register, when it
pins a typeVersion that node does not offer, or when a workflow the import
reported as successful is not in the instance afterwards.

Standard library only: no dependencies, no network, no n8n API key.
"""
import argparse
import glob
import json
import os
import sys

DEFAULT_TYPE_VERSION = 1


def display(path):
    """The path as a reader of the CI log would type it."""
    try:
        return os.path.relpath(path)
    except ValueError:
        return path


def load_exports(paths):
    """Every *.json under the given files and directories, parsed."""
    files = []
    for path in paths:
        if os.path.isdir(path):
            files.extend(sorted(glob.glob(os.path.join(path, "*.json"))))
        else:
            files.append(path)
    exports = []
    for path in files:
        with open(path, encoding="utf-8") as handle:
            exports.append((path, json.load(handle)))
    return exports


def check_node_types(exports, registry, problems):
    """Every node type and typeVersion in the exports is one the build has."""
    for path, workflow in exports:
        for node in workflow.get("nodes", []):
            name = node.get("name", "<unnamed>")
            node_type = node.get("type")
            if node_type not in registry:
                problems.append(
                    f"{display(path)}: node {name!r} has type {node_type!r}, "
                    f"which this n8n build does not register")
                continue
            version = node.get("typeVersion", DEFAULT_TYPE_VERSION)
            known = registry[node_type]
            if version not in known:
                offered = ", ".join(str(v) for v in sorted(known))
                problems.append(
                    f"{display(path)}: node {name!r} pins {node_type} "
                    f"typeVersion {version}, which this n8n build does not "
                    f"offer. It has {offered}")


def check_listing(exports, listing, problems):
    """Every exported workflow is in the instance after the import."""
    imported = set()
    for line in listing.splitlines():
        if "|" in line:
            imported.add(line.split("|", 1)[1].strip())
    for path, workflow in exports:
        name = workflow.get("name")
        if name not in imported:
            problems.append(
                f"{display(path)}: the instance holds no workflow named "
                f"{name!r} after the import")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--registry", required=True,
                        help="node types the n8n build registers, as JSON")
    parser.add_argument("--listing", required=True,
                        help="output of `n8n list:workflow` after the import")
    parser.add_argument("exports", nargs="+",
                        help="export files, or directories of them")
    args = parser.parse_args()

    with open(args.registry, encoding="utf-8") as handle:
        registry = {name: set(versions) for name, versions in json.load(handle).items()}
    with open(args.listing, encoding="utf-8") as handle:
        listing = handle.read()

    exports = load_exports(args.exports)
    if not exports:
        print("no exports to check", file=sys.stderr)
        return 1

    problems = []
    check_node_types(exports, registry, problems)
    check_listing(exports, listing, problems)

    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        print(f"\n{len(problems)} problem(s) in {len(exports)} export(s)", file=sys.stderr)
        return 1

    nodes = sum(len(workflow.get("nodes", [])) for _, workflow in exports)
    print(f"{len(exports)} export(s), {nodes} nodes: every type and version "
          f"resolved, every workflow present")
    return 0


if __name__ == "__main__":
    sys.exit(main())
