# Design

## Source of truth
- Status: Active
- Last refreshed: 2026-07-26
- Primary product surfaces: local studio, character editor, pose editor, scene controls, render composer, backend status, render history
- Evidence reviewed: the supplied FormFrame Studio product and architecture brief; repository was otherwise empty

## Brand
- Personality: cinematic, precise, calm, technically capable, creator-first
- Trust signals: explicit local/remote boundaries, visible backend state, reproducible job metadata, honest simulation labels
- Avoid: node graphs, generic admin dashboards, neon cyberpunk, dense engineering controls in the primary flow

## Product goals
- Goals: create a character locally, pose body and face, frame a shot, produce a portable conditioning bundle, submit one render action, preserve project and history
- Non-goals: production GNM/SMPL-X inference without model assets, arbitrary ComfyUI workflows, multiview/video, normals, segmentation, garment simulation
- Success signals: the full studio is understandable without documentation; pose updates are immediate; save/load survives restart; each render creates a valid `.ffjob`

## Personas and jobs
- Primary personas: visual creators, character artists, solo filmmakers, technical image-generation users
- User jobs: stage a repeatable character; direct pose/expression/camera; keep the technical render stack out of the creative flow
- Key contexts of use: localhost on a Mac, mouse/trackpad, intermittent Colab availability

## Information architecture
- Primary navigation: Character, Pose, Scene, Render
- Core routes/screens: one studio surface with mode-specific inspector; render history remains visible throughout
- Content hierarchy: project and runtime status, viewport, creative inspector, history and job evidence

## Design principles
- Direct manipulation first: every adjustment should be visible in the viewport without a network round trip
- Progressive precision: useful presets first, granular sliders second, infrastructure detail last
- Honest system state: simulated/local preview, remote readiness, progress, failures, and persisted outputs are clearly distinguished
- Tradeoffs: a procedural guide character proves interaction and data contracts now; licensed model adapters replace it later

## Visual language
- Color: ink-black studio shell, warm bone canvas, saffron action color, muted moss success, restrained coral error
- Typography: geometric display face with legible system sans fallback; compact uppercase labels for studio metadata
- Spacing/layout rhythm: 4/8px base, broad 20–28px panel padding, deliberate breathing room around the viewport
- Shape/radius/elevation: 10–18px radii, thin warm borders, soft grounded shadows, no glassmorphism
- Motion: short 160–240ms state transitions; progress and selection motion only
- Imagery/iconography: procedural 3D character and conditioning thumbnails; thin line icons

## Components
- Existing components to reuse: none
- New/changed components: StudioShell, TopBar, ModeRail, Viewport, Inspector, SliderField, SegmentedControl, RuntimePill, RenderHistory, ConditioningStrip
- Variants and states: selected, hover, focus, disabled, offline, provisioning, ready, rendering, completed, failed
- Token/component ownership: CSS custom properties in `apps/mac-ui/src/app.css`; reusable Svelte components in `apps/mac-ui/src/lib`

## Accessibility
- Target standard: WCAG 2.2 AA where applicable
- Keyboard/focus behavior: all controls native and tab-accessible; visible saffron focus ring; buttons expose labels
- Contrast/readability: high-contrast text; muted text remains readable against panel surfaces
- Screen-reader semantics: landmarks, labelled controls, live status regions, semantic buttons and headings
- Reduced motion and sensory considerations: respect `prefers-reduced-motion`; no flashing or autoplay media

## Responsive behavior
- Supported breakpoints/devices: desktop-first at 1280px+, usable tablet/compact desktop down to 820px
- Layout adaptations: inspector becomes full-width below viewport; history stacks; rail converts to horizontal tabs
- Touch/hover differences: controls remain at least 40px high; hover is never the sole carrier of information

## Interaction states
- Loading: skeleton-free, explicit status copy and compact progress bar
- Empty: guided first project and first render prompts
- Error: plain-language message with local data safety reassurance
- Success: render card with preview, bundle path, and reproducibility metadata
- Disabled: visibly muted with explanatory title where useful
- Offline/slow network: local editing and project save remain enabled; remote render clearly blocked or simulated

## Content voice
- Tone: calm, direct, craft-oriented
- Terminology: say character, pose, frame, render, guide, backend; keep GNM, SMPL-X, ComfyUI, Cloudflare in technical detail areas
- Microcopy rules: short verbs, explicit local/remote labels, never imply a simulated result is neural output

## Implementation constraints
- Framework/styling system: Svelte + Vite + Babylon.js; FastAPI local controller; plain CSS tokens
- Design-token constraints: one repo-owned token layer; no third-party component system
- Performance constraints: viewport edits must not trigger API calls; render history thumbnails are compact
- Compatibility constraints: current macOS browsers; Python 3.9+; Node 20+
- Test/screenshot expectations: backend API and bundle tests; production frontend build; desktop and compact visual smoke checks

## Open questions
- [ ] Select and license the production body model before distribution / product owner / commercial release blocker
- [ ] Supply GNM and body-model assets plus canonical alignment data / engineering / real geometry adapter
- [ ] Choose the pinned image checkpoint and control adapters / rendering / real Colab output
- [ ] Configure Colab and Cloudflare credentials / infrastructure / live remote render

