import { ArcRotateCamera } from "@babylonjs/core/Cameras/arcRotateCamera";
import { Engine } from "@babylonjs/core/Engines/engine";
import { DirectionalLight } from "@babylonjs/core/Lights/directionalLight";
import { HemisphericLight } from "@babylonjs/core/Lights/hemisphericLight";
import { ShadowGenerator } from "@babylonjs/core/Lights/Shadows/shadowGenerator";
import "@babylonjs/core/Lights/Shadows/shadowGeneratorSceneComponent";
import { Color3, Color4 } from "@babylonjs/core/Maths/math.color";
import { Vector3 } from "@babylonjs/core/Maths/math.vector";
import { StandardMaterial } from "@babylonjs/core/Materials/standardMaterial";
import { MeshBuilder } from "@babylonjs/core/Meshes/meshBuilder";
import { TransformNode } from "@babylonjs/core/Meshes/transformNode";
import { Scene } from "@babylonjs/core/scene";
import { SceneLoader } from "@babylonjs/core/Loading/sceneLoader";
import "@babylonjs/loaders/glTF";

const radians = (degrees) => (degrees * Math.PI) / 180;

function material(scene, name, color) {
  const result = new StandardMaterial(name, scene);
  result.diffuseColor = color;
  result.specularColor = new Color3(0.04, 0.04, 0.04);
  return result;
}

function createLimb(scene, name, parent, position, length, radius, limbMaterial) {
  const joint = new TransformNode(`${name}-joint`, scene);
  joint.parent = parent;
  joint.position.copyFrom(position);
  const mesh = MeshBuilder.CreateCapsule(name, { height: length, radius, tessellation: 18 }, scene);
  mesh.parent = joint;
  mesh.position.y = -length / 2;
  mesh.material = limbMaterial;
  return { joint, mesh };
}

export function createStudioScene(canvas) {
  const engine = new Engine(canvas, true, { preserveDrawingBuffer: true, stencil: true });
  const scene = new Scene(engine);
  scene.clearColor = Color4.FromHexString("#D8CFC1FF");
  scene.environmentIntensity = 0.72;

  const camera = new ArcRotateCamera("camera", radians(84), radians(80), 5.8, new Vector3(0, 1.5, 0), scene);
  camera.lowerRadiusLimit = 3.4;
  camera.upperRadiusLimit = 9;
  camera.lowerBetaLimit = radians(48);
  camera.upperBetaLimit = radians(112);
  camera.wheelDeltaPercentage = 0.012;
  camera.panningSensibility = 0;
  camera.attachControl(canvas, true);

  const hemi = new HemisphericLight("fill", new Vector3(0, 1, 0), scene);
  hemi.diffuse = Color3.FromHexString("#F7E9D5");
  hemi.groundColor = Color3.FromHexString("#596066");
  hemi.intensity = 0.58;

  const key = new DirectionalLight("key", new Vector3(-0.65, -1, 0.5), scene);
  key.position = new Vector3(3, 6, -4);
  key.diffuse = Color3.FromHexString("#FFE3B0");
  key.intensity = 2.6;
  const shadows = new ShadowGenerator(1024, key);
  shadows.useBlurExponentialShadowMap = true;
  shadows.blurKernel = 24;
  shadows.darkness = 0.22;

  const floorMaterial = material(scene, "floor-material", Color3.FromHexString("#9B9085"), 0.94);
  const floor = MeshBuilder.CreateDisc("floor", { radius: 3.2, tessellation: 80 }, scene);
  floor.rotation.x = Math.PI / 2;
  floor.position.y = 0.015;
  floor.material = floorMaterial;
  floor.receiveShadows = true;

  const root = new TransformNode("character-root", scene);
  let productionMeshes = [];
  let activeMeshUrl = "";
  const pelvis = new TransformNode("pelvis-joint", scene);
  pelvis.parent = root;
  pelvis.position.y = 1.03;
  const torsoJoint = new TransformNode("torso-joint", scene);
  torsoJoint.parent = pelvis;
  torsoJoint.position.y = 0.28;

  const skinMaterial = material(scene, "skin-material", Color3.FromHexString("#B9795B"), 0.74);
  const outfitMaterial = material(scene, "outfit-material", Color3.FromHexString("#242523"), 0.88);
  const hairMaterial = material(scene, "hair-material", Color3.FromHexString("#2A211E"), 0.96);
  const eyeMaterial = material(scene, "eye-material", Color3.FromHexString("#EBE4D8"), 0.5);
  const irisMaterial = material(scene, "iris-material", Color3.FromHexString("#3D3026"), 0.6);

  const pelvisMesh = MeshBuilder.CreateSphere("pelvis", { diameter: 0.56, segments: 24 }, scene);
  pelvisMesh.parent = pelvis;
  pelvisMesh.scaling = new Vector3(1.1, 0.64, 0.78);
  pelvisMesh.material = outfitMaterial;

  const torso = MeshBuilder.CreateCapsule("torso", { height: 1.08, radius: 0.32, tessellation: 24 }, scene);
  torso.parent = torsoJoint;
  torso.position.y = 0.36;
  torso.scaling = new Vector3(1.04, 1, 0.72);
  torso.material = outfitMaterial;

  const neck = createLimb(scene, "neck", torsoJoint, new Vector3(0, 0.93, 0), 0.25, 0.105, skinMaterial);
  const headJoint = new TransformNode("head-joint", scene);
  headJoint.parent = neck.joint;
  headJoint.position.y = 0.12;
  const head = MeshBuilder.CreateSphere("head", { diameter: 0.53, segments: 32 }, scene);
  head.parent = headJoint;
  head.position.y = 0.22;
  head.scaling = new Vector3(0.88, 1.08, 0.9);
  head.material = skinMaterial;

  const hair = MeshBuilder.CreateSphere("hair", { diameter: 0.55, segments: 28, slice: 0.62 }, scene);
  hair.parent = headJoint;
  hair.position.y = 0.3;
  hair.scaling = new Vector3(0.91, 0.72, 0.93);
  hair.material = hairMaterial;

  const jaw = MeshBuilder.CreateSphere("jaw", { diameter: 0.29, segments: 20 }, scene);
  jaw.parent = headJoint;
  jaw.position = new Vector3(0, 0.08, -0.17);
  jaw.scaling = new Vector3(1.15, 0.55, 0.58);
  jaw.material = skinMaterial;

  const leftEye = MeshBuilder.CreateSphere("left-eye", { diameter: 0.07, segments: 16 }, scene);
  leftEye.parent = headJoint;
  leftEye.position = new Vector3(-0.1, 0.26, -0.235);
  leftEye.material = eyeMaterial;
  const rightEye = leftEye.clone("right-eye");
  rightEye.position.x = 0.1;
  const leftIris = MeshBuilder.CreateSphere("left-iris", { diameter: 0.033, segments: 12 }, scene);
  leftIris.parent = headJoint;
  leftIris.position = new Vector3(-0.1, 0.26, -0.268);
  leftIris.material = irisMaterial;
  const rightIris = leftIris.clone("right-iris");
  rightIris.position.x = 0.1;

  const shoulderY = 0.78;
  const leftUpperArm = createLimb(scene, "left-upper-arm", torsoJoint, new Vector3(-0.36, shoulderY, 0), 0.7, 0.115, outfitMaterial);
  const rightUpperArm = createLimb(scene, "right-upper-arm", torsoJoint, new Vector3(0.36, shoulderY, 0), 0.7, 0.115, outfitMaterial);
  const leftForearm = createLimb(scene, "left-forearm", leftUpperArm.joint, new Vector3(0, -0.67, 0), 0.62, 0.095, skinMaterial);
  const rightForearm = createLimb(scene, "right-forearm", rightUpperArm.joint, new Vector3(0, -0.67, 0), 0.62, 0.095, skinMaterial);
  const leftHand = MeshBuilder.CreateSphere("left-hand", { diameter: 0.18, segments: 18 }, scene);
  leftHand.parent = leftForearm.joint;
  leftHand.position.y = -0.63;
  leftHand.scaling.y = 1.35;
  leftHand.material = skinMaterial;
  const rightHand = leftHand.clone("right-hand");
  rightHand.parent = rightForearm.joint;

  const leftThigh = createLimb(scene, "left-thigh", pelvis, new Vector3(-0.17, 0, 0), 0.88, 0.165, outfitMaterial);
  const rightThigh = createLimb(scene, "right-thigh", pelvis, new Vector3(0.17, 0, 0), 0.88, 0.165, outfitMaterial);
  const leftShin = createLimb(scene, "left-shin", leftThigh.joint, new Vector3(0, -0.84, 0), 0.86, 0.13, outfitMaterial);
  const rightShin = createLimb(scene, "right-shin", rightThigh.joint, new Vector3(0, -0.84, 0), 0.86, 0.13, outfitMaterial);
  const leftFoot = MeshBuilder.CreateCapsule("left-foot", { height: 0.42, radius: 0.13, tessellation: 18 }, scene);
  leftFoot.parent = leftShin.joint;
  leftFoot.rotation.x = Math.PI / 2;
  leftFoot.position = new Vector3(0, -0.88, -0.11);
  leftFoot.material = outfitMaterial;
  const rightFoot = leftFoot.clone("right-foot");
  rightFoot.parent = rightShin.joint;

  scene.meshes
    .filter((mesh) => mesh !== floor)
    .forEach((mesh) => shadows.addShadowCaster(mesh));

  const backgrounds = {
    "Warm seamless": ["#D8CFC1", "#9B9085"],
    "Slate studio": ["#9DA4A5", "#5C6467"],
    "Night cyclorama": ["#303238", "#202328"]
  };
  const outfits = {
    "Studio black": "#242523",
    "Field jacket": "#4A5540",
    "Bone tailoring": "#D4C7B2"
  };

  function update(project) {
    const { character, pose, scene: sceneState } = project;
    root.scaling.y = character.height;
    root.position.x = pose.hip_shift * 0.24;
    pelvis.rotation.z = radians(pose.hip_shift * -15);
    torsoJoint.rotation.y = radians(pose.torso_twist);
    torso.scaling.x = 0.9 + character.shoulder_width * 0.28;
    torso.scaling.z = 0.62 + character.build * 0.22;
    pelvisMesh.scaling.x = 0.92 + character.build * 0.35;
    headJoint.rotation.y = radians(pose.head_turn);
    headJoint.rotation.z = radians(pose.head_tilt);
    jaw.scaling.y = 0.48 + pose.expression_strength * 0.18;
    leftUpperArm.joint.rotation.z = radians(pose.left_arm);
    rightUpperArm.joint.rotation.z = radians(-pose.right_arm);
    leftForearm.joint.rotation.z = radians(-pose.left_elbow);
    rightForearm.joint.rotation.z = radians(pose.right_elbow);
    leftThigh.joint.rotation.z = radians(-4 - pose.hip_shift * 12);
    rightThigh.joint.rotation.z = radians(4 - pose.hip_shift * 12);
    leftShin.joint.rotation.x = radians(-pose.left_knee);
    rightShin.joint.rotation.x = radians(-pose.right_knee);
    leftIris.position.x = -0.1 + pose.gaze_x * 0.014;
    rightIris.position.x = 0.1 + pose.gaze_x * 0.014;
    leftIris.position.y = 0.26 + pose.gaze_y * 0.014;
    rightIris.position.y = 0.26 + pose.gaze_y * 0.014;
    skinMaterial.diffuseColor = Color3.FromHexString(character.appearance.skin_tone);
    outfitMaterial.diffuseColor = Color3.FromHexString(outfits[character.appearance.outfit] || outfits["Studio black"]);
    const [background, floorColor] = backgrounds[sceneState.background] || backgrounds["Warm seamless"];
    scene.clearColor = Color4.FromHexString(`${background}FF`);
    floorMaterial.diffuseColor = Color3.FromHexString(floorColor);
    floor.isVisible = sceneState.floor_visible;
    key.intensity = 0.5 + sceneState.key_light * 3;
    hemi.intensity = 0.22 + sceneState.fill_light * 1.25;
    camera.alpha = radians(90 + sceneState.camera_yaw);
    camera.beta = radians(80 - sceneState.camera_pitch);
    camera.radius = sceneState.camera_distance;
    camera.fov = 2 * Math.atan(18 / sceneState.focal_length);
  }

  engine.runRenderLoop(() => scene.render());
  const onResize = () => engine.resize();
  window.addEventListener("resize", onResize);

  return {
    update,
    async loadProductionMesh(meshUrl) {
      if (meshUrl === activeMeshUrl) return;
      activeMeshUrl = meshUrl;
      productionMeshes.forEach((mesh) => mesh.dispose());
      productionMeshes = [];
      root.setEnabled(!meshUrl);
      if (!meshUrl) return;
      try {
        const result = await SceneLoader.ImportMeshAsync("", "", meshUrl, scene);
        if (activeMeshUrl !== meshUrl) {
          result.meshes.forEach((mesh) => mesh.dispose());
          return;
        }
        productionMeshes = result.meshes;
        productionMeshes.forEach((mesh) => {
          if (mesh !== floor) shadows.addShadowCaster(mesh);
        });
      } catch {
        if (activeMeshUrl === meshUrl) {
          activeMeshUrl = "";
          root.setEnabled(true);
        }
      }
    },
    resetCamera() {
      camera.alpha = radians(82);
      camera.beta = radians(80);
      camera.radius = 5.8;
      camera.setTarget(new Vector3(0, 1.5, 0));
    },
    capture() {
      return canvas.toDataURL("image/png");
    },
    dispose() {
      window.removeEventListener("resize", onResize);
      scene.dispose();
      engine.dispose();
    }
  };
}
