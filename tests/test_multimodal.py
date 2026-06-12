"""Tests for Multi-modal Support — image analysis."""

import sys, os, tempfile, base64
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from devagent.agentic.multimodal import (
    ImageDescription, ImageReadTool,
    ScreenshotAnalyzer, MultimodalContextBuilder,
    MultimodalModelRouter,
)
from devagent.agentic.tools import PathSandbox


def make_test_image(ws: str) -> str:
    """Create a minimal 1x1 PNG test image."""
    # Smallest valid PNG (1x1 red pixel)
    png_data = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/5+hHgAHggJ/PchI7wAAAABJRU5ErkJggg=="
    )
    path = os.path.join(ws, "error_screenshot.png")
    with open(path, "wb") as f:
        f.write(png_data)
    return path


class TestImageDescription:
    def test_defaults(self):
        desc = ImageDescription(
            path="test.png", detected_type="error_screenshot",
            summary="An error", key_details="Division by zero"
        )
        assert desc.detected_type == "error_screenshot"


class TestImageReadTool:
    def test_unsupported_format(self):
        import asyncio
        ws = tempfile.mkdtemp()
        with open(os.path.join(ws, "test.txt"), "w") as f:
            f.write("not an image")
        tool = ImageReadTool()
        result = asyncio.run(tool.execute({"path": "test.txt"}, ws))
        assert not result.success

    def test_missing_image(self):
        import asyncio
        ws = tempfile.mkdtemp()
        tool = ImageReadTool()
        result = asyncio.run(tool.execute({"path": "nonexistent.png"}, ws))
        assert not result.success
        assert "not found" in result.error

    def test_valid_png(self):
        import asyncio
        ws = tempfile.mkdtemp()
        path = make_test_image(ws)
        tool = ImageReadTool()
        result = asyncio.run(tool.execute(
            {"path": "error_screenshot.png", "query": "error message"}, ws
        ))
        # Should succeed even without multimodal LLM (fallback)
        assert result.success


class TestScreenshotAnalyzer:
    def test_detect_type_error(self):
        t = ScreenshotAnalyzer._detect_type("find the error in this crash screenshot")
        assert t == "error_screenshot"

    def test_detect_type_ui(self):
        t = ScreenshotAnalyzer._detect_type("check the layout and css")
        assert t == "ui_mockup"

    def test_detect_type_arch(self):
        t = ScreenshotAnalyzer._detect_type("analyze this system architecture diagram")
        assert t == "architecture_diagram"

    def test_detect_type_log(self):
        t = ScreenshotAnalyzer._detect_type("what does this terminal log show")
        assert t == "log_output"

    def test_fallback_analyze(self):
        analyzer = ScreenshotAnalyzer()
        ws = tempfile.mkdtemp()
        path = make_test_image(ws)
        with open(path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()
        desc = analyzer._fallback_analyze(img_b64, ".png",
                                          "error_screenshot", path)
        assert desc.detected_type == "error_screenshot"
        assert "Image file" in desc.summary


class TestMultimodalContextBuilder:
    def test_empty(self):
        builder = MultimodalContextBuilder()
        msgs = builder.build([])
        assert msgs == []

    def test_with_description(self):
        builder = MultimodalContextBuilder()
        desc = ImageDescription(
            path="error.png", detected_type="error_screenshot",
            summary="Division by zero error", key_details="Line 42"
        )
        msgs = builder.build([desc])
        assert len(msgs) == 1
        assert "error_screenshot" in msgs[0]["content"]


class TestMultimodalModelRouter:
    def test_select_best(self):
        model = MultimodalModelRouter.select("high", ["openai"])
        assert model == "gpt-4o"

    def test_select_cheap(self):
        model = MultimodalModelRouter.select("low", ["openai"])
        assert model == "gpt-4o-mini"

    def test_no_provider(self):
        model = MultimodalModelRouter.select("high", ["nonexistent"])
        assert model is None

    def test_is_multimodal_available(self):
        assert MultimodalModelRouter.is_multimodal_available(
            {"provider": "openai", "model": "gpt-4o"}
        )
        assert not MultimodalModelRouter.is_multimodal_available(
            {"provider": "openai", "model": "gpt-3.5-turbo"}
        )
