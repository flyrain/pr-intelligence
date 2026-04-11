from __future__ import annotations

import json
import subprocess
import sys


def test_llm_public_import_does_not_expose_internal_provider_modules() -> None:
    script = """
import json
import polaris_pr_intel.llm as llm

public_names = sorted(name for name in dir(llm) if not name.startswith("__"))
print(json.dumps(public_names))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )
    public_names = json.loads(result.stdout)

    assert "LLMAdapter" in public_names
    assert "SUPPORTED_LLM_PROVIDERS" in public_names
    assert "build_llm_adapter" in public_names
    assert "_heuristic" not in public_names
    assert "_claude_code_local" not in public_names
    assert "_codex_local" not in public_names

