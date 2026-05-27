"""Tests for vision perception module."""
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from xagent.core.vision.base import UIElement, UIPerception
from xagent.core.vision.multimodal_perceptor import MultimodalPerceptor
from xagent.core.vision.perceptor import VisionPerceptor
from xagent.core.vision.code_fusion import VisualCodeFusion
from xagent.core.code_intel.indexer import CodeIndexer


class TestUIElement:
    def test_to_markdown(self):
        e = UIElement(element_id="b1", element_type="button", label="Submit",
                      clickable=True, state="focused")
        md = e.to_markdown()
        assert "button" in md
        assert "Submit" in md
        assert "focused" in md
        assert "clickable" in md


class TestUIPerception:
    def test_to_context_string(self):
        p = UIPerception(
            source="a11y",
            ui_type="browser_page",
            title="Login",
            elements=[
                UIElement("i1", "input", "Username", editable=True),
                UIElement("b1", "button", "Login", clickable=True),
            ],
        )
        ctx = p.to_context_string()
        assert "Login" in ctx
        assert "Username" in ctx
        assert "button" in ctx

    def test_find_elements(self):
        p = UIPerception(
            source="test",
            ui_type="desktop",
            elements=[
                UIElement("b1", "button", "Save"),
                UIElement("b2", "button", "Cancel"),
                UIElement("i1", "input", "Name"),
            ],
        )
        buttons = p.find(element_type="button")
        assert len(buttons) == 2
        save = p.find(label_contains="Save")
        assert len(save) == 1

    def test_max_elements_limit(self):
        p = UIPerception(
            source="test",
            ui_type="desktop",
            elements=[UIElement(f"e{i}", "text", f"Item {i}") for i in range(50)],
        )
        ctx = p.to_context_string(max_elements=10)
        assert "(40 more elements)" in ctx


class TestMultimodalPerceptor:
    def test_is_vision_model_detection(self):
        assert MultimodalPerceptor.is_vision_model("gpt-4o") is True
        assert MultimodalPerceptor.is_vision_model("gpt-4o-mini") is True
        assert MultimodalPerceptor.is_vision_model("gemini-1.5-pro") is True
        assert MultimodalPerceptor.is_vision_model("claude-3-5-sonnet") is True
        assert MultimodalPerceptor.is_vision_model("gpt-3.5-turbo") is False
        assert MultimodalPerceptor.is_vision_model("") is False

    def test_unavailable_without_llm(self):
        p = MultimodalPerceptor(None)
        assert p.is_available() is False
        result = p.perceive("screen")
        assert "No vision model" in result.title

    def test_unavailable_with_text_model(self):
        llm = MagicMock()
        llm.model_id = "gpt-3.5-turbo"
        llm.provider = "openai"
        p = MultimodalPerceptor(llm)
        assert p.is_available() is False

    def test_available_with_vision_model(self):
        llm = MagicMock()
        llm.model_id = "gpt-4o"
        llm.provider = "openai"
        p = MultimodalPerceptor(llm)
        assert p.is_available() is True

    def test_parse_elements_from_description(self):
        desc = """
The page has a login form.
- Button: Submit
- Input field: Username
- Input field: Password
- Link: Forgot password?
"""
        elems = MultimodalPerceptor._parse_elements_from_description(desc)
        assert len(elems) >= 3
        types = {e.element_type for e in elems}
        assert "button" in types
        assert "input" in types
        assert "link" in types


class TestVisionPerceptorAuto:
    def test_auto_selects_a11y_for_browser(self):
        p = VisionPerceptor(strategy="auto")
        # 无 browser page，无法选 a11y
        strategy = p._select_strategy("screen", {})
        assert strategy in ("hybrid", "multimodal", "none")

    def test_forced_strategy(self):
        llm = MagicMock()
        llm.model_id = "gpt-4o"
        p = VisionPerceptor(llm_client=llm, strategy="multimodal")
        assert p.get_strategy() == "multimodal"

    def test_is_available(self):
        p = VisionPerceptor()
        # PIL 应该可用
        assert p.is_available() is True


class TestVisualCodeFusion:
    def test_trace_ui_to_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "app.py").write_text("class LoginButton:\n    pass\n")
            indexer = CodeIndexer(tmp)
            indexer.index_all()
            fusion = VisualCodeFusion(indexer)

            perception = UIPerception(
                source="test",
                ui_type="browser",
                elements=[UIElement("b1", "button", "LoginButton")],
            )
            locs = fusion.trace_ui_to_code(perception)
            assert len(locs) >= 1
            assert "LoginButton" in locs[0].snippet

    def test_no_indexer_returns_empty(self):
        fusion = VisualCodeFusion()
        perception = UIPerception(source="test", ui_type="browser")
        assert fusion.trace_ui_to_code(perception) == []

    def test_extract_search_terms(self):
        p = UIPerception(
            source="test",
            ui_type="browser",
            elements=[UIElement("b1", "button", "SubmitForm")],
            raw_text="Click SubmitForm to continue. The Dashboard loads.",
        )
        terms = VisualCodeFusion._extract_search_terms(p, "")
        assert "SubmitForm" in terms
        assert "Dashboard" in terms  # 大写开头词

    def test_find_style_for_element(self):
        with tempfile.TemporaryDirectory() as tmp:
            css_file = Path(tmp) / "styles.css"
            css_file.write_text(".login-btn { color: blue; }\n.form-input { border: 1px solid; }\n")
            indexer = CodeIndexer(tmp)
            indexer.index_all()
            fusion = VisualCodeFusion(indexer)

            perception = UIPerception(
                source="test",
                ui_type="browser",
                elements=[UIElement("b1", "button", "login-btn")],
            )
            locs = fusion.find_style_for_element(perception, "b1")
            assert len(locs) >= 1
            assert any("login-btn" in loc.match_reason for loc in locs)

    def test_match_by_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            jsx_file = Path(tmp) / "LoginForm.jsx"
            jsx_file.write_text("function LoginForm() { return <div><input/><input/><button/></div>; }")
            indexer = CodeIndexer(tmp)
            indexer.index_all()
            fusion = VisualCodeFusion(indexer)

            perception = UIPerception(
                source="test",
                ui_type="browser",
                elements=[
                    UIElement("i1", "input", "Username"),
                    UIElement("i2", "input", "Password"),
                    UIElement("b1", "button", "Submit"),
                ],
            )
            locs = fusion.match_by_layout(perception)
            assert len(locs) >= 1
            assert "LoginForm" in locs[0].snippet

    def test_boost_by_element_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "app.py").write_text("class LoginButton:\n    pass\nclass DataTable:\n    pass\n")
            indexer = CodeIndexer(tmp)
            indexer.index_all()
            fusion = VisualCodeFusion(indexer)

            perception = UIPerception(
                source="test",
                ui_type="browser",
                elements=[UIElement("b1", "button", "Login")],
            )
            locs = fusion.trace_ui_to_code(perception)
            # LoginButton 应该因为 button 类型而获得更高置信度
            btn_locs = [l for l in locs if "LoginButton" in l.snippet]
            assert len(btn_locs) >= 1
            assert btn_locs[0].confidence > 0.5
