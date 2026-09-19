"""M14 Chunk B tests (SPEC 014-player-ux, T4): UI form + portrait flow."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

APP_JS = os.path.join(
    os.path.dirname(__file__), "../../backend/app/web/app.js")
STYLE_CSS = os.path.join(
    os.path.dirname(__file__), "../../backend/app/web/style.css")


def load_js():
    with open(APP_JS, encoding="utf-8") as fh:
        return fh.read()


class TestCreationForm:
    def test_textareas_present(self):
        js = load_js()
        assert 'placeholder: "Внешность (необязательно)"' in js
        assert 'placeholder: "Биография (необязательно)"' in js
        assert 'el("label", {}, "Внешность"), looks,' in js
        assert 'el("label", {}, "Биография"), biography,' in js

    def test_post_body_sends_fields(self):
        js = load_js()
        assert "looks: looks.value.trim() || undefined" in js
        assert "biography: biography.value.trim() || undefined" in js


class TestPortraitFlow:
    def test_portrait_calls_visual_api(self):
        js = load_js()
        # generate: POST portraits, then asset file
        assert "`/visual/portraits/${S.character.id}`" in js
        assert "`/visual/assets/${r.asset.id}/file`" in js
        # canonical reuse on load
        assert "`/visual/characters/${S.character.id}/portrait`" in js

    def test_blob_objecturl_flow(self):
        js = load_js()
        assert "URL.createObjectURL(blob)" in js
        assert "fetchPortraitBlob" in js
        # Authorization header is sent (img src cannot carry headers)
        assert "Authorization: `Bearer ${token}`" in js

    def test_graceful_degradation(self):
        js = load_js()
        # visual.enabled=false → canonical fetch 403/404 → button stays
        assert "/* no portrait yet — keep button */" in js
        # generation error surfaces in status, no crash
        assert "portrait-status" in js

    def test_world_view_embeds_portrait(self):
        js = load_js()
        assert "const portraitBox = portraitBlock();" in js
        assert "portraitBox," in js

    def test_css_rules(self):
        with open(STYLE_CSS, encoding="utf-8") as fh:
            css = fh.read()
        assert ".portrait .portrait-img" in css
        assert "textarea" in css
