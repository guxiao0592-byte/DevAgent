"""Multi-modal Support — image analysis for DevAgent V2.

Implements design doc 13: ImageReadTool, ScreenshotAnalyzer (4 types),
MultimodalContextBuilder, and MultimodalModelRouter.

Supports: error screenshots, UI mockups, architecture diagrams, log outputs.
"""

import os
import base64
import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

from .tools import BaseTool, ToolResult, PathSandbox


# ============================================================================
# Data Types
# ============================================================================

@dataclass
class ImageDescription:
    path: str
    detected_type: str   # error_screenshot | ui_mockup | architecture_diagram | log_output | unknown
    summary: str
    key_details: str
    relevant_code_hint: str = ""
    width: int = 0
    height: int = 0
    format: str = ""


# ============================================================================
# ImageReadTool
# ============================================================================

class ImageReadTool(BaseTool):
    """Read and analyze image files for software engineering context."""
    name = "image_read"
    description = "Read and analyze an image file. Supports screenshots, diagrams, and UI mockups. Use to understand visual errors, layouts, or architecture."
    parameters = {
        "path": {
            "type": "string",
            "description": "Path to image file (PNG, JPG, GIF, WebP, BMP)"
        },
        "query": {
            "type": "string",
            "description": "What to look for: 'error message', 'layout issue', 'component structure', or 'general description'",
            "default": "general description"
        }
    }

    SUPPORTED_FORMATS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

    def __init__(self, llm_client=None):
        self.name = ImageReadTool.name
        self.description = ImageReadTool.description
        self.parameters = ImageReadTool.parameters
        self.llm = llm_client
        self.analyzer = ScreenshotAnalyzer(llm_client)

    async def execute(self, params: dict, workspace: str) -> ToolResult:
        sandbox = PathSandbox(workspace)
        file_path = sandbox.resolve(params["path"])
        query = params.get("query", "general description")

        if not file_path.exists():
            return ToolResult(False, error=f"Image not found: {params['path']}")

        ext = file_path.suffix.lower()
        if ext not in self.SUPPORTED_FORMATS:
            return ToolResult(False,
                error=f"Unsupported format: {ext}. Supported: {self.SUPPORTED_FORMATS}")

        # Encode image
        try:
            image_data = base64.b64encode(file_path.read_bytes()).decode()
        except Exception as e:
            return ToolResult(False, error=f"Failed to read image: {e}")

        # Analyze
        description = await self.analyzer.analyze(image_data, ext, query, str(file_path))

        if description:
            output = self._format_description(description)
            return ToolResult(True, output, structured={
                "detected_type": description.detected_type,
                "summary": description.summary,
                "path": params["path"]
            })

        return ToolResult(False, error="Image analysis failed — no multimodal model available")


    @staticmethod
    def _format_description(desc: ImageDescription) -> str:
        lines = [
            f"## Image Analysis: {desc.path}",
            f"Type: {desc.detected_type}",
            f"Format: {desc.format} ({desc.width}x{desc.height})",
            f"",
            f"### Summary",
            desc.summary,
            f"",
            f"### Key Details",
            desc.key_details,
        ]
        if desc.relevant_code_hint:
            lines.append(f"\n### Relevant Code\n{desc.relevant_code_hint}")
        return "\n".join(lines)


# ============================================================================
# Screenshot Analyzer
# ============================================================================

class ScreenshotAnalyzer:
    """Specialized analysis for different screenshot types."""

    ANALYSIS_PROMPTS = {
        "error_screenshot": """This is a screenshot of a software error. Analyze carefully:
1. What is the EXACT error message shown?
2. What file and line number are mentioned (if any)?
3. Is this a runtime error, build error, or test failure?
4. What is the likely root cause?
5. What code change would fix it?""",

        "ui_mockup": """This is a UI mockup or application screenshot. Analyze:
1. What UI components are shown (buttons, forms, tables, etc.)?
2. Are there any visual issues (overlap, misalignment, broken layout)?
3. Does the styling look correct (colors, spacing, fonts)?
4. What CSS or component changes would fix visual problems?""",

        "architecture_diagram": """This is an architecture or system design diagram. Analyze:
1. What components, services, or modules are shown?
2. What are the data flows and dependencies?
3. What technology choices are indicated?
4. Does this match the current codebase structure?""",

        "log_output": """This is a terminal log or console output. Analyze:
1. What errors or warnings are visible?
2. What is the sequence of events shown?
3. What is the most likely root cause?
4. What is the suggested fix?"""
    }

    def __init__(self, llm_client=None):
        self.llm = llm_client

    async def analyze(self, image_b64: str, ext: str, query: str,
                      file_path: str = "") -> Optional[ImageDescription]:
        """Analyze an image using available multimodal capabilities."""
        detected_type = self._detect_type(query)

        # If we have a multimodal LLM, use it
        if self.llm and MultimodalModelRouter.is_multimodal_available(
            getattr(self.llm, 'config', {})
        ):
            return await self._llm_analyze(image_b64, ext, detected_type, file_path)

        # Fallback: file-based analysis (size, extension, filename hints)
        return self._fallback_analyze(image_b64, ext, detected_type, file_path)

    async def _llm_analyze(self, image_b64: str, ext: str,
                            detected_type: str, file_path: str) -> ImageDescription:
        prompt = self.ANALYSIS_PROMPTS.get(detected_type,
                 "Describe this image in detail for software engineering context.")

        try:
            response = self.llm.chat_with_image(
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url",
                         "image_url": {"url": f"data:image/{ext.lstrip('.')};base64,{image_b64}"}}
                    ]
                }],
                max_tokens=800
            )
        except (AttributeError, Exception):
            return self._fallback_analyze(image_b64, ext, detected_type, file_path)

        return ImageDescription(
            path=file_path,
            detected_type=detected_type,
            summary=response[:500],
            key_details=response[:1000],
            format=ext,
        )

    def _fallback_analyze(self, image_b64: str, ext: str,
                           detected_type: str, file_path: str) -> ImageDescription:
        """Fallback analysis based on file metadata and filename heuristics."""
        import io
        try:
            data = base64.b64decode(image_b64)
            size = len(data)
        except Exception:
            size = 0

        # Guess content from filename
        name_lower = os.path.basename(file_path).lower()
        if any(kw in name_lower for kw in ["error", "bug", "crash", "fail"]):
            detected_type = "error_screenshot"
        elif any(kw in name_lower for kw in ["ui", "screen", "page", "mockup"]):
            detected_type = "ui_mockup"
        elif any(kw in name_lower for kw in ["arch", "design", "diagram", "flow"]):
            detected_type = "architecture_diagram"
        elif any(kw in name_lower for kw in ["log", "terminal", "console", "output"]):
            detected_type = "log_output"

        return ImageDescription(
            path=file_path,
            detected_type=detected_type,
            summary=f"Image file ({size} bytes, {ext} format). "
                   f"Detected type: {detected_type}. "
                   f"Multimodal LLM not available — use file_read on related code files.",
            key_details=f"File: {file_path}\nSize: {size} bytes\nFormat: {ext}\n"
                       f"Consider reading related source files for context.",
            format=ext,
        )

    @staticmethod
    def _detect_type(query: str) -> str:
        q = query.lower()
        if any(kw in q for kw in ["error", "bug", "crash", "exception", "fail"]):
            return "error_screenshot"
        if any(kw in q for kw in ["ui", "layout", "design", "css", "style", "component"]):
            return "ui_mockup"
        if any(kw in q for kw in ["arch", "diagram", "flow", "system", "structure"]):
            return "architecture_diagram"
        if any(kw in q for kw in ["log", "terminal", "console", "output"]):
            return "log_output"
        return "error_screenshot"


# ============================================================================
# Multimodal Context Builder
# ============================================================================

class MultimodalContextBuilder:
    """Injects image analysis results into the agent's context."""

    def build(self, descriptions: list[ImageDescription]) -> list[dict]:
        if not descriptions:
            return []

        parts = ["## Image Analysis Results"]
        for i, desc in enumerate(descriptions, 1):
            parts.append(f"""### Image {i}: {os.path.basename(desc.path)}
- **Type**: {desc.detected_type}
- **Summary**: {desc.summary[:300]}
- **Key Details**: {desc.key_details[:300]}
- **Relevant Code Hint**: {desc.relevant_code_hint or 'N/A'}
""")

        return [{"role": "user", "content": "\n".join(parts)}]


# ============================================================================
# Multimodal Model Router
# ============================================================================

class MultimodalModelRouter:
    """Selects the best available multimodal model based on task and availability."""

    MODELS = [
        {"name": "gpt-4o", "provider": "openai", "quality": "best", "cost": "high"},
        {"name": "gpt-4o-mini", "provider": "openai", "quality": "good", "cost": "low"},
        {"name": "claude-sonnet-4-6", "provider": "anthropic", "quality": "best", "cost": "high"},
        {"name": "claude-opus-4-7", "provider": "anthropic", "quality": "best", "cost": "high"},
        {"name": "deepseek-vl2", "provider": "deepseek", "quality": "adequate", "cost": "low"},
    ]

    MULTIMODAL_MODELS = {
        "gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-4-vision-preview",
        "claude-3-opus", "claude-3-sonnet", "claude-3-haiku",
        "claude-sonnet-4-6", "claude-opus-4-7", "claude-haiku-4-5",
        "deepseek-vl2", "gemini-pro-vision", "gemini-flash"
    }

    @classmethod
    def select(cls, task_complexity: str = "medium",
               available_providers: list[str] = None) -> Optional[str]:
        candidates = cls.MODELS
        if available_providers:
            candidates = [m for m in candidates if m["provider"] in available_providers]
        if task_complexity == "high":
            candidates = [m for m in candidates if m["quality"] == "best"]
        elif task_complexity == "low":
            candidates = [m for m in candidates if m["cost"] == "low"]
        return candidates[0]["name"] if candidates else None

    @classmethod
    def is_multimodal_available(cls, llm_config: dict) -> bool:
        if not llm_config:
            return False
        model = llm_config.get("model", llm_config.get("deepseek", {}).get("model", ""))
        provider = llm_config.get("provider", "")
        return model in cls.MULTIMODAL_MODELS
