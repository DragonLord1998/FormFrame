from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_gnm_expression_editor_is_a_separate_accessible_modal():
    inspector = (ROOT / "apps/mac-ui/src/lib/Inspector.svelte").read_text()
    app = (ROOT / "apps/mac-ui/src/App.svelte").read_text()
    modal = (ROOT / "apps/mac-ui/src/lib/ExpressionStudio.svelte").read_text()

    assert "Open GNM Expression Studio" in inspector
    assert 'role="dialog"' in modal
    assert 'aria-modal="true"' in modal
    assert 'event.key === "Escape"' in modal
    assert "bind:project" in app
    assert "meshUrl={geometryUrl}" in app
    assert "does not remesh the face into the body" in modal


def test_expression_viewer_isolates_and_recenters_only_the_gnm_head():
    scene = (ROOT / "apps/mac-ui/src/lib/faceStudioScene.js").read_text()

    assert '["smplx_body", "neck_connector"]' in scene
    assert 'includesName(mesh, ["gnm_head"])' in scene
    assert "mesh.position.subtractInPlace(center)" in scene
    assert 'new TransformNode("gnm-production-head-root"' in scene
    assert "loadedPose.head_turn" in scene
