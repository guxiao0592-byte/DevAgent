"""Artifact Contract — validates pipeline phase outputs against schema.

Each phase in pipeline.yaml defines required_artifacts and validators.
This module:
  1. Reads pipeline.yaml configuration
  2. Validates phase output directories against contracts
  3. Generates artifact manifests
  4. Reports compliance/violations
"""

from __future__ import annotations
import os
import json
import yaml
import re
import hashlib
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional
import fnmatch


# ============================================================================
# Data Types
# ============================================================================

@dataclass
class ArtifactInfo:
    """A single artifact produced by a phase."""
    id: str
    path: str
    type: str  # markdown, json, diagram, source, test, manifest, etc.
    size_bytes: int = 0
    hash_sha256: str = ""
    status: str = "unknown"  # valid, missing, invalid
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id, "path": self.path, "type": self.type,
            "size_bytes": self.size_bytes, "hash": self.hash_sha256,
            "status": self.status, "error": self.error,
        }


@dataclass
class PhaseContract:
    """Contract for one pipeline phase."""
    name: str
    display_name: str
    output_dir: str
    required_artifacts: list[dict] = field(default_factory=list)
    optional_artifacts: list[dict] = field(default_factory=list)
    validators: list[str] = field(default_factory=list)
    retry: dict = field(default_factory=dict)
    conditional: bool = False

    @property
    def all_required_paths(self) -> list[str]:
        return [a.get("path", "") for a in self.required_artifacts]


@dataclass
class PhaseValidationResult:
    """Result of validating a phase output against its contract."""
    phase_name: str
    is_valid: bool
    artifacts_found: list[ArtifactInfo] = field(default_factory=list)
    artifacts_missing: list[str] = field(default_factory=list)
    validator_results: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        return self.is_valid and not self.errors and not self.artifacts_missing

    def summary(self) -> str:
        found = len(self.artifacts_found)
        missing = len(self.artifacts_missing)
        failed = sum(1 for v in self.validator_results if v.get("status") != "passed")
        return (f"{self.phase_name}: {found} artifacts found, {missing} missing, "
                f"{failed} validators failed — {'PASS' if self.is_valid else 'FAIL'}")


@dataclass
class ArtifactManifest:
    """Complete manifest for a task's phase outputs."""
    task_id: str
    phase: str
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    artifacts: list[ArtifactInfo] = field(default_factory=list)
    validators: list[dict] = field(default_factory=list)
    compliance_status: str = "unknown"

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "phase": self.phase,
            "generated_at": self.generated_at,
            "artifacts": [a.to_dict() for a in self.artifacts],
            "validators": self.validators,
            "compliance_status": self.compliance_status,
        }

    def save(self, output_dir: str) -> str:
        """Save manifest to output_dir/artifact_manifest.json."""
        path = os.path.join(output_dir, "artifact_manifest.json")
        os.makedirs(output_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)
        return path


# ============================================================================
# Contract Loader
# ============================================================================

def load_pipeline_config(config_path: str = None) -> dict:
    """Load pipeline.yaml configuration."""
    if config_path is None:
        config_path = os.path.join(os.path.dirname(__file__), "..", "configs", "pipeline.yaml")
    config_path = os.path.abspath(config_path)
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_phase_contracts(config: dict = None) -> dict[str, PhaseContract]:
    """Parse pipeline.yaml into PhaseContract objects."""
    if config is None:
        config = load_pipeline_config()

    phases = config.get("phases", {})
    contracts = {}
    for name, spec in phases.items():
        contracts[name] = PhaseContract(
            name=name,
            display_name=spec.get("display_name", name),
            output_dir=spec.get("output_dir", name),
            required_artifacts=spec.get("required_artifacts", []),
            optional_artifacts=spec.get("optional_artifacts", []),
            validators=spec.get("validators", []),
            retry=spec.get("retry", {}),
            conditional=spec.get("conditional", False),
        )
    return contracts


# ============================================================================
# Artifact Scanner & Validator
# ============================================================================

class PhaseValidator:
    """Validates a phase output directory against its contract."""

    def __init__(self, contract: PhaseContract, task_id: str = ""):
        self.contract = contract
        self.task_id = task_id

    def validate_directory(self, phase_dir: str) -> PhaseValidationResult:
        """Scan a phase output directory and validate against contract."""
        if not os.path.isdir(phase_dir):
            return PhaseValidationResult(
                phase_name=self.contract.name, is_valid=False,
                errors=[f"Phase directory not found: {phase_dir}"],
            )

        # Step 1: Find artifacts
        found_artifacts = self._scan_artifacts(phase_dir)

        # Step 2: Check required artifacts
        missing = []
        for req in self.contract.required_artifacts:
            req_path = req.get("path", "")
            min_count = req.get("min_count", 1) if isinstance(req.get("min_count"), int) else \
                        req.get("min_files", 1) if isinstance(req.get("min_files"), int) else 1

            matches = [a for a in found_artifacts if self._path_matches(a.path, req_path)]
            if len(matches) < min_count:
                missing.append(f"{req_path} (found {len(matches)}, need {min_count})")

        # Step 3: Run validators
        validator_results = []
        for v in self.contract.validators:
            result = self._run_validator(v, found_artifacts, phase_dir)
            if result:
                validator_results.append(result)

        # Step 4: Determine overall validity
        all_validators_passed = all(
            v.get("status") == "passed" for v in validator_results
        )
        is_valid = len(missing) == 0 and all_validators_passed

        return PhaseValidationResult(
            phase_name=self.contract.name,
            is_valid=is_valid,
            artifacts_found=found_artifacts,
            artifacts_missing=missing,
            validator_results=validator_results,
            errors=[] if is_valid else [f"Missing: {', '.join(missing)}"],
        )

    def generate_manifest(self, phase_dir: str) -> ArtifactManifest:
        """Generate an artifact manifest for a phase directory."""
        result = self.validate_directory(phase_dir)
        return ArtifactManifest(
            task_id=self.task_id,
            phase=self.contract.name,
            artifacts=result.artifacts_found,
            validators=result.validator_results,
            compliance_status="compliant" if result.is_valid else "non_compliant",
        )

    # =========== Private ===========

    def _scan_artifacts(self, phase_dir: str) -> list[ArtifactInfo]:
        """Scan all files in a phase directory."""
        artifacts = []
        for root, dirs, files in os.walk(phase_dir):
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            for f in files:
                if f.startswith('.'):
                    continue
                fp = os.path.join(root, f)
                rel = os.path.relpath(fp, phase_dir)
                try:
                    size = os.path.getsize(fp)
                except OSError:
                    size = 0

                # Detect type
                ext = os.path.splitext(f)[1].lower()
                if ext in ('.md', '.markdown'):
                    atype = 'markdown'
                elif ext == '.json':
                    atype = 'json' if 'manifest' not in f.lower() else 'manifest'
                elif ext in ('.mmd', '.puml'):
                    atype = 'diagram'
                elif ext == '.py':
                    atype = 'source' if 'test' not in f.lower() else 'test'
                elif ext in ('.diff', '.patch'):
                    atype = 'patch'
                elif ext in ('.svg', '.png'):
                    atype = 'diagram_image'
                else:
                    atype = 'other'

                # Compute hash
                try:
                    with open(fp, 'rb') as fh:
                        h = hashlib.sha256(fh.read()).hexdigest()[:16]
                except Exception:
                    h = ""

                artifacts.append(ArtifactInfo(
                    id=f"{self.contract.name}_{rel.replace('/', '_')}",
                    path=rel, type=atype, size_bytes=size,
                    hash_sha256=h, status="valid",
                ))
        return artifacts

    def _path_matches(self, artifact_path: str, pattern: str) -> bool:
        """Check if an artifact path matches a glob pattern."""
        # Handle directory patterns ending with /
        if pattern.endswith('/'):
            return artifact_path.startswith(pattern) or \
                   artifact_path.startswith(pattern[:-1] + os.sep)

        # Handle glob patterns
        if '*' in pattern or '?' in pattern:
            return fnmatch.fnmatch(artifact_path, pattern) or \
                   fnmatch.fnmatch(os.path.basename(artifact_path), pattern)

        # Exact match or contains
        return artifact_path == pattern or pattern in artifact_path

    def _run_validator(self, validator_spec, artifacts: list[ArtifactInfo],
                       phase_dir: str) -> Optional[dict]:
        """Run a single validator."""
        if isinstance(validator_spec, str):
            name = validator_spec
            params = {}
        elif isinstance(validator_spec, dict):
            name = next(iter(validator_spec))
            params = validator_spec
        else:
            return None

        if name == "artifact_count_min" or name.startswith("artifact_count_min"):
            threshold = int(str(validator_spec).split(":")[-1]) if ":" in str(validator_spec) else \
                        params.get("min", params.get("threshold", 3))
            count = len(artifacts)
            return {"name": "artifact_count_min", "status": "passed" if count >= threshold else "failed",
                    "detail": f"{count}/{threshold} artifacts"}

        if name == "markdown_structure":
            return self._validate_markdown(phase_dir)

        if name == "json_schema":
            return self._validate_json_files(phase_dir)

        if name == "diagram_syntax":
            return self._validate_diagrams(phase_dir)

        if name == "syntax_check":
            return self._validate_python_syntax(phase_dir)

        return {"name": name, "status": "skipped", "detail": "validator not implemented"}

    def _validate_markdown(self, phase_dir: str) -> dict:
        """Check .md files have basic structure."""
        for f in os.listdir(phase_dir):
            if f.endswith('.md'):
                fp = os.path.join(phase_dir, f)
                try:
                    with open(fp, 'r') as fh:
                        content = fh.read()
                except Exception:
                    return {"name": "markdown_structure", "status": "failed",
                            "detail": f"Cannot read {f}"}

                if len(content) < 200:
                    return {"name": "markdown_structure", "status": "failed",
                            "detail": f"{f}: too short ({len(content)} chars)"}
                if not re.search(r'^# ', content, re.MULTILINE):
                    return {"name": "markdown_structure", "status": "failed",
                            "detail": f"{f}: no H1 heading"}
                # Found at least one valid markdown file
                return {"name": "markdown_structure", "status": "passed",
                        "detail": f"{f}: {len(content)} chars"}

        return {"name": "markdown_structure", "status": "failed", "detail": "no .md files found"}

    def _validate_json_files(self, phase_dir: str) -> dict:
        """Check .json files are valid."""
        for f in os.listdir(phase_dir):
            if f.endswith('.json'):
                fp = os.path.join(phase_dir, f)
                try:
                    with open(fp, 'r') as fh:
                        json.load(fh)
                    return {"name": "json_schema", "status": "passed", "detail": f"{f} valid"}
                except Exception as e:
                    return {"name": "json_schema", "status": "failed", "detail": f"{f}: {e}"}
        return {"name": "json_schema", "status": "skipped", "detail": "no .json files"}

    def _validate_diagrams(self, phase_dir: str) -> dict:
        """Check Mermaid/PUML diagram files for basic syntax."""
        found = []
        for root, _, files in os.walk(phase_dir):
            for f in files:
                if f.endswith(('.mmd', '.puml')):
                    fp = os.path.join(root, f)
                    try:
                        with open(fp, 'r') as fh:
                            code = fh.read()
                    except Exception:
                        continue
                    if len(code) >= 30:
                        found.append(f)
        if found:
            return {"name": "diagram_syntax", "status": "passed",
                    "detail": f"{len(found)} diagram files"}
        return {"name": "diagram_syntax", "status": "failed",
                "detail": "no valid diagram files"}

    def _validate_python_syntax(self, phase_dir: str) -> dict:
        """Check .py files compiile."""
        import ast
        found, failed = 0, []
        for root, _, files in os.walk(phase_dir):
            for f in files:
                if f.endswith('.py'):
                    fp = os.path.join(root, f)
                    try:
                        with open(fp, 'r') as fh:
                            ast.parse(fh.read())
                        found += 1
                    except SyntaxError as e:
                        failed.append(f"{f}:{e.lineno}")
                    except Exception:
                        pass
        if not found:
            return {"name": "syntax_check", "status": "skipped", "detail": "no .py files"}
        if failed:
            return {"name": "syntax_check", "status": "failed",
                    "detail": f"{len(failed)} files with errors: {failed[:3]}"}
        return {"name": "syntax_check", "status": "passed",
                "detail": f"{found} files OK"}
