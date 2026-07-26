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

export function newProject() {
  const now = new Date().toISOString();
  const uid = () => crypto.randomUUID().replaceAll("-", "").slice(0, 12);
  return {
    schema_version: 1,
    project_id: `project_${uid()}`,
    name: "Mara / Studio study",
    character: {
      character_id: `character_${uid()}`,
      name: "Mara",
      identity: [0.04, -0.11, 0.08],
      body_shape: [0.15, -0.07, 0.03],
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
        hair_description: "short dark sculpted hair",
        outfit: "Studio black",
        outfit_prompt: "minimal black fitted studio outfit"
      }
    },
    pose: {
      preset: "Contrapposto",
      ...posePresets["Contrapposto"],
      expression: "Quiet confidence",
      expression_strength: 0.38,
      gaze_x: 0.08,
      gaze_y: 0.02
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
        "Editorial character portrait, soft directional studio light, natural skin texture, restrained cinematic color",
      negative_prompt: "distorted anatomy, duplicate limbs, plastic skin, oversharpened",
      seed: 184627,
      width: 768,
      height: 1024,
      denoise: 0.55,
      depth_strength: 0.72,
      pose_strength: 0.34,
      quality: "Studio"
    },
    created_at: now,
    updated_at: now
  };
}
