# FormFrame Studio

Pose the form. Generate the frame.

FormFrame Studio is a local-first character staging application. The studio can
evaluate a real GNM Head plus SMPL-X body in an isolated geometry worker, display
the resulting GLB in Babylon.js, package aligned RGB/depth/pose conditioning, and
run the immutable `controlled-character-v1` workflow on a private A100 Colab
runtime. Colab CLI owns provisioning and bulk transfer; an authenticated,
managed Cloudflare Tunnel carries commands and previews. ComfyUI stays bound to
`127.0.0.1` inside Colab.

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
   - the stable Cloudflare hostname, named-tunnel token, Access audience, and
     machine-to-machine service token;
   - the generated GNM, SMPL-X, and geometry-Python paths.
3. Run `./scripts/dev-real.sh`, open the studio, and press **Start Backend**.

## Verify

```bash
./scripts/test-all.sh
```

## Implemented path

- Local character, body, scene, camera, and render controls
- Dedicated GNM Expression Studio modal for expression, head pose, and gaze;
  GNM, the neck connector, and SMPL-X remain aligned meshes rather than a
  misleading remeshed topology
- Real GNM v3 + SMPL-X worker, cached GLB export, and Babylon.js GLB display
- Explicit pose ownership: SMPL-X owns body/global head pose; GNM owns identity
  and local expression
- Project persistence on the Mac
- Conditioning Contract v1 asset generation
- Content hashes and ZIP-storage `.ffjob` bundles
- Immutable `controlled-character-v1` workflow identifier
- Official Colab CLI A100 creation/reconnect, probe, upload/download, bootstrap,
  and CLI execution fallback
- Colab bootstrap clones the GitHub source repository at an explicit
  revision before installing sources and workflows
- Measured transfer split: Colab CLI for bootstrap secrets, `.ffjob` bundles,
  reusable asset-cache misses, and final PNG downloads;
  Cloudflare for authenticated control, status, progress, and preview delivery
- Authenticated Cloudflare client and private gateway with Access JWT validation
- Private ComfyUI supervisor and pinned Z-Image Turbo + ControlNet model manifest
- Immutable two-pass pose-then-depth ComfyUI API workflow
- `FormFrameJobLoader` and `FormFrameResultSaver` custom nodes
- Runtime lifecycle, queue, progress, cancellation, preview/result endpoints
- Render history and reproducibility metadata

## Security and licensing

SMPL-X and GNM geometry stay on the Mac under ignored local model directories.
The exported `.ffjob` contains only derived RGB/depth/pose conditioning, never
the licensed model files. Those model files are not uploaded, committed, or
redistributed. The gateway accepts only the fixed FormFrame manifest/workflow
contract and never accepts shell commands or arbitrary ComfyUI JSON.

Reusable reference assets are negotiated by SHA-256 through the private gateway
and uploaded through Colab CLI only when the active Colab cache is missing them.
The local Mac remote cache stores source-revision and model-manifest metadata
only; pinned model weights rehydrate inside the active Colab session.

See [DESIGN.md](DESIGN.md) and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
