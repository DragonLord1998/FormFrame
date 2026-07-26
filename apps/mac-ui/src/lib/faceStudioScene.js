import { ArcRotateCamera } from "@babylonjs/core/Cameras/arcRotateCamera";
import { Engine } from "@babylonjs/core/Engines/engine";
import { HemisphericLight } from "@babylonjs/core/Lights/hemisphericLight";
import { Color3, Color4 } from "@babylonjs/core/Maths/math.color";
import { Vector3 } from "@babylonjs/core/Maths/math.vector";
import { StandardMaterial } from "@babylonjs/core/Materials/standardMaterial";
import { MeshBuilder } from "@babylonjs/core/Meshes/meshBuilder";
import { TransformNode } from "@babylonjs/core/Meshes/transformNode";
import { Scene } from "@babylonjs/core/scene";
import { SceneLoader } from "@babylonjs/core/Loading/sceneLoader";
import "@babylonjs/loaders/glTF";

const radians = (degrees) => (degrees * Math.PI) / 180;

const hiddenMeshNames = ["smplx_body", "neck_connector"];
const expressionNames = {
  "Quiet confidence": ["quiet", "confidence", "neutral"],
  "Soft smile": ["smile", "happy"],
  Focused: ["focus", "focused", "brow"],
  Surprised: ["surprise", "surprised", "wide"]
};

function material(scene, name, color) {
  const result = new StandardMaterial(name, scene);
  result.diffuseColor = color;
  result.specularColor = new Color3(0.06, 0.055, 0.05);
  return result;
}

function includesName(node, needles) {
  const name = `${node?.name || ""} ${node?.id || ""}`.toLowerCase();
  return needles.some((needle) => name.includes(needle));
}

function hasAncestorNamed(node, needle) {
  let parent = node?.parent;
  while (parent) {
    if (`${parent.name || ""} ${parent.id || ""}`.toLowerCase().includes(needle)) return true;
    parent = parent.parent;
  }
  return false;
}

function createFallbackHead(scene) {
  const root = new TransformNode("gnm-head-fallback-root", scene);
  root.position.y = 0.08;

  const skin = material(scene, "gnm-fallback-skin", Color3.FromHexString("#B9795B"));
  const feature = material(scene, "gnm-fallback-feature", Color3.FromHexString("#27231F"));
  const eyeWhite = material(scene, "gnm-fallback-eye-white", Color3.FromHexString("#EEE7DA"));
  const iris = material(scene, "gnm-fallback-iris", Color3.FromHexString("#3A3027"));

  const head = MeshBuilder.CreateSphere("gnm_head", { diameter: 1.15, segments: 48 }, scene);
  head.parent = root;
  head.scaling = new Vector3(0.82, 1.04, 0.76);
  head.material = skin;

  const jaw = MeshBuilder.CreateSphere("gnm_jaw_expression", { diameter: 0.55, segments: 28 }, scene);
  jaw.parent = root;
  jaw.position = new Vector3(0, -0.28, -0.38);
  jaw.scaling = new Vector3(0.82, 0.28, 0.32);
  jaw.material = skin;

  const nose = MeshBuilder.CreateSphere("gnm_nose", { diameter: 0.2, segments: 18 }, scene);
  nose.parent = root;
  nose.position = new Vector3(0, 0.02, -0.58);
  nose.scaling = new Vector3(0.58, 1.1, 0.72);
  nose.material = skin;

  const leftEye = MeshBuilder.CreateSphere("gnm_left_eye", { diameter: 0.14, segments: 18 }, scene);
  leftEye.parent = root;
  leftEye.position = new Vector3(-0.19, 0.14, -0.57);
  leftEye.scaling = new Vector3(1.18, 0.72, 0.45);
  leftEye.material = eyeWhite;
  const rightEye = leftEye.clone("gnm_right_eye");
  rightEye.position.x = 0.19;

  const leftIris = MeshBuilder.CreateSphere("gnm_left_iris", { diameter: 0.065, segments: 12 }, scene);
  leftIris.parent = root;
  leftIris.position = new Vector3(-0.19, 0.14, -0.632);
  leftIris.scaling.z = 0.32;
  leftIris.material = iris;
  const rightIris = leftIris.clone("gnm_right_iris");
  rightIris.position.x = 0.19;

  const mouth = MeshBuilder.CreateBox("gnm_mouth_expression", { width: 0.3, height: 0.025, depth: 0.012 }, scene);
  mouth.parent = root;
  mouth.position = new Vector3(0, -0.24, -0.62);
  mouth.material = feature;

  const leftBrow = MeshBuilder.CreateBox("gnm_left_brow_expression", { width: 0.24, height: 0.025, depth: 0.018 }, scene);
  leftBrow.parent = root;
  leftBrow.position = new Vector3(-0.2, 0.31, -0.57);
  leftBrow.rotation.z = radians(-5);
  leftBrow.material = feature;
  const rightBrow = leftBrow.clone("gnm_right_brow_expression");
  rightBrow.position.x = 0.2;
  rightBrow.rotation.z = radians(5);

  return {
    root,
    leftIris,
    rightIris,
    jaw,
    mouth,
    leftBrow,
    rightBrow,
    update(pose) {
      const strength = Number(pose.expression_strength || 0);
      const expression = pose.expression || "Quiet confidence";
      root.rotation.y = radians(Number(pose.head_turn || 0));
      root.rotation.z = radians(Number(pose.head_tilt || 0));
      leftIris.position.x = -0.19 + Number(pose.gaze_x || 0) * 0.035;
      rightIris.position.x = 0.19 + Number(pose.gaze_x || 0) * 0.035;
      leftIris.position.y = 0.14 + Number(pose.gaze_y || 0) * 0.035;
      rightIris.position.y = 0.14 + Number(pose.gaze_y || 0) * 0.035;
      jaw.scaling.y = 0.28 + strength * (expression === "Surprised" ? 0.22 : 0.08);
      mouth.scaling.x = 1 + strength * (expression === "Soft smile" ? 0.72 : 0.18);
      mouth.scaling.y = 1 + strength * (expression === "Surprised" ? 3.8 : 0.4);
      mouth.position.y = -0.24 + strength * (expression === "Soft smile" ? 0.045 : -0.035);
      leftBrow.rotation.z = radians(expression === "Focused" ? -14 - strength * 8 : -5 - strength * 3);
      rightBrow.rotation.z = radians(expression === "Focused" ? 14 + strength * 8 : 5 + strength * 3);
      leftBrow.position.y = 0.31 + strength * (expression === "Surprised" ? 0.09 : 0);
      rightBrow.position.y = leftBrow.position.y;
    },
    setVisible(visible) {
      root.setEnabled(visible);
    }
  };
}

function applyMorphTargets(meshes, pose) {
  const activeNeedles = expressionNames[pose.expression] || expressionNames["Quiet confidence"];
  const strength = Number(pose.expression_strength || 0);

  meshes.forEach((mesh) => {
    const manager = mesh.morphTargetManager;
    if (!manager) return;
    for (let index = 0; index < manager.numTargets; index += 1) {
      const target = manager.getTarget(index);
      const name = target.name.toLowerCase();
      const isExpression = Object.values(expressionNames).flat().some((needle) => name.includes(needle));
      if (isExpression) target.influence = activeNeedles.some((needle) => name.includes(needle)) ? strength : 0;
    }
  });
}

export function createFaceStudioScene(canvas) {
  const engine = new Engine(canvas, true, { preserveDrawingBuffer: true, stencil: true });
  const scene = new Scene(engine);
  scene.clearColor = Color4.FromHexString("#171714FF");
  scene.environmentIntensity = 0.8;

  const camera = new ArcRotateCamera("gnm-face-camera", radians(-90), radians(82), 1.8, new Vector3(0, 0.02, 0), scene);
  camera.lowerRadiusLimit = 0.9;
  camera.upperRadiusLimit = 4.2;
  camera.lowerBetaLimit = radians(54);
  camera.upperBetaLimit = radians(110);
  camera.wheelDeltaPercentage = 0.012;
  camera.panningSensibility = 0;
  camera.attachControl(canvas, true);

  const fill = new HemisphericLight("gnm-face-fill", new Vector3(0.2, 1, -0.2), scene);
  fill.diffuse = Color3.FromHexString("#F4DEC2");
  fill.groundColor = Color3.FromHexString("#343632");
  fill.intensity = 1.05;

  const fallback = createFallbackHead(scene);
  const productionHeadRoot = new TransformNode("gnm-production-head-root", scene);
  let productionMeshes = [];
  let activeMeshUrl = "";
  let currentPose = {};
  let loadedPose = {};

  function update(project) {
    currentPose = project?.pose || {};
    fallback.update(currentPose);
    productionHeadRoot.rotation.y = radians(
      Number(currentPose.head_turn || 0) - Number(loadedPose.head_turn || 0)
    );
    productionHeadRoot.rotation.z = radians(
      Number(currentPose.head_tilt || 0) - Number(loadedPose.head_tilt || 0)
    );
    applyMorphTargets(productionMeshes, currentPose);
  }

  engine.runRenderLoop(() => scene.render());
  const onResize = () => engine.resize();
  window.addEventListener("resize", onResize);

  return {
    update,
    async loadGnmHead(meshUrl) {
      if (meshUrl === activeMeshUrl) return;
      activeMeshUrl = meshUrl || "";
      productionMeshes.forEach((mesh) => mesh.dispose());
      productionMeshes = [];
      fallback.setVisible(true);
      if (!meshUrl) return;

      try {
        const result = await SceneLoader.ImportMeshAsync("", "", meshUrl, scene);
        if (activeMeshUrl !== meshUrl) {
          result.meshes.forEach((mesh) => mesh.dispose());
          return;
        }

        const hasGnmHead = [...result.meshes, ...(result.transformNodes || [])].some((node) => includesName(node, ["gnm_head"]));
        productionMeshes = result.meshes;
        const headMeshes = productionMeshes.filter(
          (mesh) =>
            mesh.name !== "__root__"
            && (includesName(mesh, ["gnm_head"]) || hasAncestorNamed(mesh, "gnm_head"))
            && !includesName(mesh, hiddenMeshNames)
        );
        productionMeshes.forEach((mesh) => {
          if (mesh.name === "__root__") {
            mesh.setEnabled(true);
            return;
          }
          const keepHead = hasGnmHead && headMeshes.includes(mesh);
          mesh.setEnabled(keepHead && !includesName(mesh, hiddenMeshNames));
        });
        if (headMeshes.length) {
          let minimum = headMeshes[0].getBoundingInfo().boundingBox.minimum.clone();
          let maximum = headMeshes[0].getBoundingInfo().boundingBox.maximum.clone();
          headMeshes.slice(1).forEach((mesh) => {
            const bounds = mesh.getBoundingInfo().boundingBox;
            minimum = Vector3.Minimize(minimum, bounds.minimum);
            maximum = Vector3.Maximize(maximum, bounds.maximum);
          });
          const center = minimum.add(maximum).scale(0.5);
          const extent = maximum.subtract(minimum);
          headMeshes.forEach((mesh) => {
            mesh.parent = productionHeadRoot;
            mesh.position.subtractInPlace(center);
          });
          camera.alpha = radians(-90);
          camera.radius = Math.max(0.95, Math.max(extent.x, extent.y, extent.z) * 1.9);
          camera.setTarget(Vector3.Zero());
        }
        loadedPose = { ...currentPose };
        productionHeadRoot.rotation.set(0, 0, 0);
        fallback.setVisible(!hasGnmHead);
        update({ pose: currentPose });
      } catch {
        if (activeMeshUrl === meshUrl) {
          activeMeshUrl = "";
          productionMeshes = [];
          fallback.setVisible(true);
        }
      }
    },
    dispose() {
      window.removeEventListener("resize", onResize);
      scene.dispose();
      engine.dispose();
    }
  };
}
