# FormFrame Studio

Pose the form. Generate the frame.

FormFrame Studio is a local-first character staging application. The studio can
evaluate a real GNM Head plus SMPL-X body in an isolated geometry worker, display
the resulting GLB in Babylon.js, package aligned RGB/depth/pose conditioning, and
run the immutable `controlled-character-v1` workflow on a private A100 Colab
runtime. Colab CLI owns provisioning and bulk transfer; an authenticated,
Cloudflare Tunnel carries commands and previews. The default stable path uses a
managed Tunnel + Access; live validation can use an ephemeral Quick Tunnel with
a fresh strong bearer token. ComfyUI stays bound to `127.0.0.1` inside Colab.

## Run

```bash
./scripts/setup.sh
./scripts/dev.sh
```

Open [http://127.0.0.1:7860](http://127.0.0.1:7860).

The default command retains an explicit local-preview development option. For
the real backend:

1. Sign in at the official SMPL-X site, download the licensed model archive,
   and run:

   ```bash
   FORMFRAME_GEOMETRY_BOOTSTRAP_PYTHON=/path/to/python3.12 \
     ./scripts/install-geometry.sh /path/to/downloaded/models/smplx
   ```

2. Copy `.env.example` to `.env` and configure:
   - the official Google Colab CLI and ADC authentication;
   - the GitHub repository URL and exact commit revision Colab should
     clone into `/content/formframe/source`;
   - either `FORMFRAME_CF_TUNNEL_MODE=quick` for an account-free ephemeral test
     route, or the stable Cloudflare hostname, named-tunnel token, Access
     audience, and machine-to-machine service token for managed mode;
   - the generated GNM, SMPL-X, and geometry-Python paths.
3. Run `./scripts/dev-real.sh`, open the studio, and press **Start Backend**.

## Verify

```bash
./scripts/test-all.sh
```

Each exported job writes a deterministic `conditioning-contact-sheet.png` beside
the `.ffjob` bundle and records its hash under `output.local_validation` in the
job manifest. That sheet is local conditioning evidence only. To prepare an A-F
live A100 comparison manifest, run:

```bash
python scripts/create-comparison-matrix.py --manifest path/to/jobs/<job_id>/manifest.json
```

To verify that the configured Colab account can allocate the required A100
without leaving compute running, source `.env` and run:

```bash
python scripts/validate-colab-a100.py
```

The script provisions or reconnects the exact `formframe-a100` session, verifies
the reported GPU and VRAM, and stops that same session in a `finally` block.

## Implemented path

- Local character, body, scene, camera, and render controls
- Dedicated GNM Expression Studio modal for expression, head pose, and gaze;
  GNM, the neck connector, and SMPL-X remain aligned meshes rather than a
  misleading remeshed topology
- Real GNM v3 + SMPL-X worker, cached GLB export, and Babylon.js GLB display
- Explicit pose ownership: SMPL-X owns body/global head pose; GNM owns identity
  and local expression
- Project persistence on the Mac with character, expression, outfit, hair-proxy,
  and coarse garment-proxy selections
- Local attachment and safetensors validation for trained Z-Image Turbo identity
  LoRAs, with trigger-token and strength controls saved per character
- Conditioning Contract v1 asset generation
- Local RGB/depth/pose/optional-normal conditioning contact-sheet validation
- Three-pose real-model validation via `python scripts/validate-local-poses.py`,
  which saves RGB, depth, pose, contact-sheet, and hash evidence under the
  ignored local `data/validation/real-geometry-poses` directory
- Pending A-F comparison-matrix scaffold for later live A100 benchmark evidence
- Content hashes and ZIP-storage `.ffjob` bundles
- Immutable `controlled-character-v1` workflow identifier
- Official Colab CLI A100 creation/reconnect, probe, upload/download, bootstrap,
  and CLI execution fallback
- Colab bootstrap clones the GitHub source repository at an explicit
  revision before installing sources and workflows
- Measured transfer split: Colab CLI for bootstrap secrets, `.ffjob` bundles,
  reusable asset-cache misses, and final PNG downloads;
  Cloudflare for authenticated control, status, progress, and preview delivery
- Authenticated Cloudflare client and private gateway with managed Access JWT
  validation or a per-runtime strong bearer token in Quick Tunnel mode
- Private ComfyUI supervisor and pinned Z-Image Turbo + ControlNet model manifest
- Immutable Z-Image Turbo workflow with an optional `LoadZImageLora` stage and
  two-pass pose-then-depth ControlNet sampling
- `FormFrameJobLoader` and `FormFrameResultSaver` custom nodes
- Runtime lifecycle, queue, progress, cancellation, preview/result endpoints
- Explicit `Save compute / Stop A100` control that targets only the configured
  FormFrame Colab session and refuses to interrupt an active render
- One-shot Colab session recovery and pinned-runtime rehydration before a failed
  render is surfaced
- Render history and reproducibility metadata

## Security and licensing

SMPL-X and GNM geometry stay on the Mac under ignored local model directories.
The exported `.ffjob` contains only derived RGB/depth/pose conditioning, never
the licensed model files. Those model files are not uploaded, committed, or
redistributed. The gateway accepts only the fixed FormFrame manifest/workflow
contract and never accepts shell commands or arbitrary ComfyUI JSON.

Reusable reference assets are negotiated by SHA-256 through the private gateway
and uploaded through Colab CLI only when the active Colab cache is missing them.
After that staging step, the uploaded `.ffjob` omits the reference bytes while
preserving their immutable manifest entries; the gateway and ComfyUI job loader
rehash the corresponding remote cache files before accepting the render.
An attached trained identity LoRA remains in the Mac content-addressed asset
store, is hash-pinned in the job manifest without entering the `.ffjob`, and is
uploaded directly into ComfyUI through Colab CLI. Cloudflare carries only its
metadata. Raw references are labelled as awaiting training until a trained LoRA
is attached.
The local Mac remote cache stores source-revision and model-manifest metadata
only; pinned model weights rehydrate inside the active Colab session.

Quick Tunnel mode is intentionally limited to live validation. It discovers the
ephemeral `https://*.trycloudflare.com` URL from the pinned remote
`cloudflared` process, disables public FastAPI documentation, and requires a
fresh bearer token on HTTP and WebSocket routes. Managed Tunnel + Access remains
the stable production configuration.

See [DESIGN.md](DESIGN.md) and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
