export const posePresets = {
  "Contrapposto": {
    torso_twist: -8,
    head_turn: 12,
    head_tilt: -3,
    left_arm: -18,
    right_arm: 24,
    left_elbow: 22,
    right_elbow: 48,
    hip_shift: -0.12,
    left_knee: 4,
    right_knee: 18
  },
  "Editorial lean": {
    torso_twist: 14,
    head_turn: -18,
    head_tilt: 7,
    left_arm: 8,
    right_arm: -34,
    left_elbow: 62,
    right_elbow: 28,
    hip_shift: 0.2,
    left_knee: 24,
    right_knee: 2
  },
  "Open stance": {
    torso_twist: 2,
    head_turn: 0,
    head_tilt: 0,
    left_arm: -34,
    right_arm: 34,
    left_elbow: 14,
    right_elbow: 14,
    hip_shift: 0,
    left_knee: 2,
    right_knee: 2
  },
  "Walking beat": {
    torso_twist: -16,
    head_turn: 8,
    head_tilt: -2,
    left_arm: 28,
    right_arm: -24,
    left_elbow: 18,
    right_elbow: 22,
    hip_shift: -0.08,
    left_knee: 38,
    right_knee: 5
  }
};

export const characterPresets = {
  "Mara / Studio": {
    name: "Mara",
    identity: [0.04, -0.11, 0.08],
    body_shape: [0.15, -0.07, 0.03],
    height: 1,
    build: 0.48,
    shoulder_width: 0.52,
    leg_length: 0.55,
    appearance: {
      apparent_age: 32,
      skin_tone: "#B9795B",
      skin_description: "warm medium skin with natural texture"
    }
  },
  "Noor / Editorial": {
    name: "Noor",
    identity: [-0.02, 0.06, 0.12],
    body_shape: [0.04, 0.11, -0.05],
    height: 1.04,
    build: 0.42,
    shoulder_width: 0.46,
    leg_length: 0.62,
    appearance: {
      apparent_age: 28,
      skin_tone: "#8F5F47",
      skin_description: "deep warm skin with soft studio highlights"
    }
  },
  "Iris / Atelier": {
    name: "Iris",
    identity: [0.11, -0.02, -0.06],
    body_shape: [-0.06, 0.03, 0.09],
    height: 0.96,
    build: 0.54,
    shoulder_width: 0.58,
    leg_length: 0.49,
    appearance: {
      apparent_age: 41,
      skin_tone: "#D0A17F",
      skin_description: "fair warm skin with natural texture"
    }
  }
};

export const hairProxyLibrary = {
  "Sculpted crop": {
    hair_proxy: "hair_proxy_sculpted_crop",
    hair_description: "short dark sculpted hair"
  },
  "Soft bob": {
    hair_proxy: "hair_proxy_soft_bob",
    hair_description: "jaw-length soft bob with controlled volume"
  },
  "Pulled back": {
    hair_proxy: "hair_proxy_pulled_back",
    hair_description: "clean pulled-back hair with a compact silhouette"
  }
};

export const outfitLibrary = {
  "Studio black": {
    garment_proxy: "garment_proxy_studio_black",
    outfit_prompt: "minimal black fitted studio outfit"
  },
  "Field jacket": {
    garment_proxy: "garment_proxy_field_jacket",
    outfit_prompt: "olive field jacket over a dark fitted base layer"
  },
  "Bone tailoring": {
    garment_proxy: "garment_proxy_bone_tailoring",
    outfit_prompt: "bone-colored tailored jacket with clean editorial lines"
  }
};

export const smplxJointNames = [
  "Left hip",
  "Right hip",
  "Spine 1",
  "Left knee",
  "Right knee",
  "Spine 2",
  "Left ankle",
  "Right ankle",
  "Spine 3",
  "Left foot",
  "Right foot",
  "Neck",
  "Left collar",
  "Right collar",
  "Head",
  "Left shoulder",
  "Right shoulder",
  "Left elbow",
  "Right elbow",
  "Left wrist",
  "Right wrist"
];

export const smplxHandJointNames = [
  "Index 1",
  "Index 2",
  "Index 3",
  "Middle 1",
  "Middle 2",
  "Middle 3",
  "Pinky 1",
  "Pinky 2",
  "Pinky 3",
  "Ring 1",
  "Ring 2",
  "Ring 3",
  "Thumb 1",
  "Thumb 2",
  "Thumb 3"
];

export const smplxAxes = ["X", "Y", "Z"];

const vector = (length, seed = []) =>
  Array.from({ length }, (_, index) => Number(seed[index] ?? 0));

export const defaultSmplxState = () => ({
  body_shape: vector(10),
  smplx_body_pose: vector(21 * 3),
  smplx_left_hand_pose: vector(15 * 3),
  smplx_right_hand_pose: vector(15 * 3),
  smplx_global_orient: vector(3)
});

export const normalizeVector = (value, length) => {
  const source = Array.isArray(value) ? value : [];
  return Array.from({ length }, (_, index) => {
    const number = Number(source[index]);
    return Number.isFinite(number) ? number : 0;
  });
};

const normalizeIdentityLora = (value) => {
  if (!value || typeof value !== "object") return null;
  const strength = Number(value.strength);
  return {
    ...value,
    filename: value.filename || "identity.safetensors",
    trigger_token: value.trigger_token || "ff_identity",
    strength: Number.isFinite(strength) ? strength : 0.75
  };
};

export const normalizeProject = (project) => ({
  ...project,
  character: {
    ...project.character,
    identity_lora: normalizeIdentityLora(project?.character?.identity_lora),
    identity: normalizeVector(project?.character?.identity, 253),
    body_shape: normalizeVector(project?.character?.body_shape, 10)
  },
  pose: {
    ...project.pose,
    gnm_expression: normalizeVector(project?.pose?.gnm_expression, 383),
    gnm_joint_rotations: normalizeVector(project?.pose?.gnm_joint_rotations, 12),
    smplx_body_pose: normalizeVector(project?.pose?.smplx_body_pose, 21 * 3),
    smplx_left_hand_pose: normalizeVector(project?.pose?.smplx_left_hand_pose, 15 * 3),
    smplx_right_hand_pose: normalizeVector(project?.pose?.smplx_right_hand_pose, 15 * 3),
    smplx_global_orient: normalizeVector(project?.pose?.smplx_global_orient, 3)
  }
});

export function newProject() {
  const now = new Date().toISOString();
  const uid = () => crypto.randomUUID().replaceAll("-", "").slice(0, 12);
  return {
    schema_version: 1,
    project_id: `project_${uid()}`,
    name: "Mara / Studio study",
    character: {
      character_id: `character_${uid()}`,
      preset: "Mara / Studio",
      name: "Mara",
      identity: vector(253, [0.04, -0.11, 0.08]),
      identity_lora: null,
      body_shape: vector(10, [0.15, -0.07, 0.03]),
      height: 1,
      build: 0.48,
      shoulder_width: 0.52,
      leg_length: 0.55,
      references: [],
      appearance: {
        apparent_age: 32,
        skin_tone: "#B9795B",
        skin_description: "warm medium skin with natural texture",
        hair_style: "Sculpted crop",
        hair_proxy: "hair_proxy_sculpted_crop",
        hair_description: "short dark sculpted hair",
        outfit: "Studio black",
        garment_proxy: "garment_proxy_studio_black",
        outfit_prompt: "minimal black fitted studio outfit"
      }
    },
    pose: {
      preset: "Contrapposto",
      ...posePresets["Contrapposto"],
      expression: "Quiet confidence",
      expression_strength: 0.38,
      gaze_x: 0.08,
      gaze_y: 0.02,
      gnm_expression: vector(383),
      gnm_joint_rotations: vector(12),
      smplx_body_pose: vector(21 * 3),
      smplx_left_hand_pose: vector(15 * 3),
      smplx_right_hand_pose: vector(15 * 3),
      smplx_global_orient: vector(3)
    },
    scene: {
      camera_yaw: -8,
      camera_pitch: 3,
      camera_distance: 5.8,
      focal_length: 70,
      frame: "portrait",
      key_light: 0.78,
      fill_light: 0.28,
      background: "Warm seamless",
      floor_visible: true
    },
    render: {
      prompt:
        "Full-body editorial character photograph, entire figure visible head to toe, centered studio composition, soft directional light, natural skin texture, restrained cinematic color",
      negative_prompt:
        "mannequin, skeleton, doll, robot, metallic body, cropped body, close-up, headshot, distorted anatomy, duplicate limbs, plastic skin, oversharpened",
      seed: 184627,
      width: 768,
      height: 1024,
      denoise: 0.55,
      depth_strength: 0.85,
      pose_strength: 0.65,
      quality: "Studio"
    },
    created_at: now,
    updated_at: now
  };
}
