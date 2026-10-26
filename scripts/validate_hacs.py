"""Validate with the exact HACS 2.0.5 upstream schema, without importing HACS.

Supply a pristine checkout at the pinned SHA. No network or credentials used.
Only the named schema/function AST nodes are evaluated, never the HACS package.
"""

import argparse
import ast
import json
from pathlib import Path
import subprocess

from awesomeversion import AwesomeVersion
from homeassistant.helpers.config_validation import url
import voluptuous as vol

HACS_SHA = "c0dfd8b44297c3673c21973e2539375a53687a9c"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hacs-source", type=Path, required=True)
    args = parser.parse_args()
    checkout = args.hacs_source.resolve()
    revision = subprocess.check_output(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True
    ).strip()
    if revision != HACS_SHA:
        parser.error("HACS source must be the pinned 2.0.5 commit")
    root = Path(__file__).resolve().parents[1]
    env = {"vol": vol, "AwesomeVersion": AwesomeVersion, "url_validator": url}
    files = {
        "custom_components/hacs/const.py": {"LOCALE"},
        "custom_components/hacs/utils/validate.py": {
            "_country_validator",
            "HACS_MANIFEST_JSON_SCHEMA",
            "INTEGRATION_MANIFEST_JSON_SCHEMA",
        },
    }
    for name, wanted in files.items():
        # Read committed blobs, not a potentially modified checkout.
        source = subprocess.check_output(
            ["git", "-C", str(checkout), "show", f"{HACS_SHA}:{name}"], text=True
        )
        nodes = []
        for node in ast.parse(source).body:
            if isinstance(node, ast.FunctionDef) and node.name in wanted:
                nodes.append(node)
            elif isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id in wanted for t in node.targets
            ):
                nodes.append(node)
        assert len(nodes) == len(wanted), "Upstream schema nodes missing"
        exec(compile(ast.Module(body=nodes, type_ignores=[]), name, "exec"), env)
    hacs = json.loads((root / "hacs.json").read_text())
    manifest = json.loads((root / "custom_components/google_pollen/manifest.json").read_text())
    env["HACS_MANIFEST_JSON_SCHEMA"](hacs)
    env["INTEGRATION_MANIFEST_JSON_SCHEMA"](manifest)
    assert not hacs["zip_release"] and not hacs["content_in_root"]
    assert "country" not in hacs and "filename" not in hacs
    assert AwesomeVersion(hacs["homeassistant"]).valid
    assert list((root / "custom_components").glob("*/manifest.json")) == [
        root / "custom_components/google_pollen/manifest.json"
    ]
    print(
        f"PASS: HACS 2.0.5 schemas ({HACS_SHA}), source layout, version, no country/zip restriction"
    )


if __name__ == "__main__":
    main()
