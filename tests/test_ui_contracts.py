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


def test_character_library_controls_are_first_class_project_state():
    inspector = (ROOT / "apps/mac-ui/src/lib/Inspector.svelte").read_text()
    project = (ROOT / "apps/mac-ui/src/lib/project.js").read_text()

    assert "export const characterPresets" in project
    assert "export const outfitLibrary" in project
    assert "export const hairProxyLibrary" in project
    assert 'preset: "Mara / Studio"' in project
    assert 'garment_proxy: "garment_proxy_studio_black"' in project
    assert 'hair_proxy: "hair_proxy_sculpted_crop"' in project
    assert "applyCharacterPreset" in inspector
    assert "applyHairProxy" in inspector
    assert "applyOutfit" in inspector
    assert "bind:value={project.character.preset}" in inspector
    assert "...hairProxyLibrary[name]" in inspector
    assert "...outfitLibrary[name]" in inspector


def test_smplx_body_studio_modal_is_wired_without_replacing_friendly_pose_controls():
    app = (ROOT / "apps/mac-ui/src/App.svelte").read_text()
    inspector = (ROOT / "apps/mac-ui/src/lib/Inspector.svelte").read_text()
    modal = (ROOT / "apps/mac-ui/src/lib/BodyStudio.svelte").read_text()

    assert 'import BodyStudio from "./lib/BodyStudio.svelte"' in app
    assert "let bodyStudioOpen = false" in app
    assert "normalizeProject" in app
    assert "onOpenBody={() => (bodyStudioOpen = true)}" in app
    assert "Open SMPL-X Body Studio" in inspector
    assert "Field label=\"Torso twist\"" in inspector
    assert "Object.keys(posePresets)" in inspector
    assert 'role="dialog"' in modal
    assert 'aria-modal="true"' in modal
    assert 'event.key === "Escape"' in modal
    assert "SMPL-X Body Studio" in modal
    assert "controlsPerPage = 18" in modal
    assert "Search shape, body, hand, or orientation" in modal


def test_smplx_dense_vector_defaults_and_control_counts_are_explicit():
    project = (ROOT / "apps/mac-ui/src/lib/project.js").read_text()
    modal = (ROOT / "apps/mac-ui/src/lib/BodyStudio.svelte").read_text()

    assert "body_shape: vector(10)" in project
    assert "smplx_body_pose: vector(21 * 3)" in project
    assert "smplx_left_hand_pose: vector(15 * 3)" in project
    assert "smplx_right_hand_pose: vector(15 * 3)" in project
    assert "smplx_global_orient: vector(3)" in project
    assert "normalizeVector" in project
    assert "project?.character?.body_shape" in project
    assert "project[control.scope][control.key]" in modal
    assert "smplxJointNames" in modal
    assert "smplxHandJointNames" in modal
    assert "<strong>10</strong><span>shape betas</span>" in modal
    assert "<strong>63</strong><span>body axes</span>" in modal
    assert "<strong>45</strong><span>left hand axes</span>" in modal
    assert "<strong>45</strong><span>right hand axes</span>" in modal
    assert "<strong>3</strong><span>global axes</span>" in modal


def test_gnm_expression_studio_exposes_full_advanced_coefficient_surface():
    modal = (ROOT / "apps/mac-ui/src/lib/ExpressionStudio.svelte").read_text()

    assert "{ length: 253 }" in modal
    assert "identity_basis_${String(index).padStart(3, \"0\")}" in modal
    assert "{ length: 100 }, (_, index) => `left_eye_region_${String(index).padStart(3, \"0\")}`" in modal
    assert "{ length: 100 }, (_, index) => `right_eye_region_${String(index).padStart(3, \"0\")}`" in modal
    assert "{ length: 150 }, (_, index) => `lower_face_region_${String(index).padStart(3, \"0\")}`" in modal
    assert '"tongue_mean"' in modal
    assert "{ length: 31 }, (_, index) => `tongue_${String(index).padStart(3, \"0\")}`" in modal
    assert '"pupils_000"' in modal
    assert "identity: normalizeArray(project.character?.identity, 253)" in modal
    assert "gnm_expression: Array(383).fill(0)" in modal
    assert 'let coefficientSearch = ""' in modal
    assert "const pageSize = 24" in modal
    assert 'aria-label="Search GNM coefficients"' in modal
    assert "filteredCoefficients.slice(pageStart, pageStart + pageSize)" in modal


def test_gnm_expression_studio_reassigns_project_for_gnm_array_reactivity():
    modal = (ROOT / "apps/mac-ui/src/lib/ExpressionStudio.svelte").read_text()

    assert 'const axisNames = ["x", "y", "z"]' in modal
    assert 'const gnmJointNames = ["neck", "head", "left_eye", "right_eye"]' in modal
    assert 'const lockedGnmJoints = new Set(["neck", "head"])' in modal
    assert "gnm_joint_rotations: Array(12).fill(0)" in modal
    assert "const updateCoefficient = (bank, index, value) => {" in modal
    assert "const next = [...project[scope][field]]" in modal
    assert "[scope]: { ...project[scope], [field]: next }" in modal
    assert "const updateJointRotation = (joint, axis, value) => {" in modal
    assert "if (lockedGnmJoints.has(joint)) return" in modal
    assert "project = { ...project, pose: project.pose }" in modal
    assert "SMPL-X owns global neck and head orientation" in modal
    assert "Only GNM eye joint rotations are editable" in modal
    assert "These convenience controls rotate the SMPL-X neck and head chain" in modal
    assert "They do not rotate GNM neck or head joints" in modal
    assert "disabled={lockedGnmJoints.has(joint)}" in modal
    assert 'aria-label={`${joint} ${axis} rotation`}' in modal
