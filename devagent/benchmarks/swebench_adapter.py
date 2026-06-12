"""SWE-bench adapter for standardized bug-fix evaluation.

This module provides integration with SWE-bench evaluation harness format.
It generates predictions.jsonl in the format expected by the official
SWE-bench evaluation framework: https://github.com/princeton-nlp/SWE-bench
"""

import json
import os
from typing import Optional


class SWEbenchAdapter:
    """Adapter to format DevAgent repair outputs as SWE-bench predictions."""

    def __init__(self, model_name: str = "DevAgent-v1"):
        self.model_name = model_name

    def create_prediction(self, instance_id: str, patch_content: str) -> dict:
        """Create a single prediction record in SWE-bench format."""
        return {
            "instance_id": instance_id,
            "model_name_or_path": self.model_name,
            "model_patch": patch_content
        }

    def create_empty_prediction(self, instance_id: str) -> dict:
        """Create a prediction record with empty patch (failed)."""
        return {
            "instance_id": instance_id,
            "model_name_or_path": self.model_name,
            "model_patch": ""
        }

    def export_predictions(self, predictions: list[dict], output_path: str):
        """Export predictions to JSONL file."""
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w") as f:
            for pred in predictions:
                f.write(json.dumps(pred) + "\n")
        return output_path

    def load_predictions(self, file_path: str) -> list[dict]:
        """Load predictions from JSONL file."""
        predictions = []
        with open(file_path, "r") as f:
            for line in f:
                if line.strip():
                    predictions.append(json.loads(line))
        return predictions

    @staticmethod
    def parse_harness_result(result: dict) -> dict:
        """Parse SWE-bench harness evaluation result."""
        return {
            "resolved": result.get("resolved", False),
            "instance_id": result.get("instance_id", ""),
            "patch_applies": result.get("patch_applies_cleanly", False),
            "test_result": result.get("test_result", {}),
            "git_diffs_applied": result.get("git_diffs_applied", []),
            "error": result.get("error", None)
        }

    def to_benchmark_result(self, results: list[dict]) -> dict:
        """Aggregate harness results into benchmark summary."""
        total = len(results)
        resolved = sum(1 for r in results if r.get("resolved"))
        errors = sum(1 for r in results if r.get("error"))

        return {
            "dataset": "SWE-bench",
            "model": self.model_name,
            "total_instances": total,
            "resolved": resolved,
            "resolution_rate": round(resolved / max(total, 1) * 100, 1),
            "error_count": errors,
            "error_rate": round(errors / max(total, 1) * 100, 1),
            "details": results
        }
