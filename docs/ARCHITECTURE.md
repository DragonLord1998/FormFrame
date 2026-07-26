# Architecture

FormFrame Studio uses the architecture in the supplied brief while keeping unavailable external systems explicit.

```text
Svelte + Babylon studio :7860
           |
           | localhost JSON + WebSocket
           v
FastAPI local controller :8000
  | projects/*.ffproject     Mac source of truth
  | conditioning exporter    RGB + depth + projected pose
  | .ffjob writer            ZIP storage + hashes
  | runtime manager          one running job
  + adapter boundaries
      | Colab CLI            provisioning and bulk transfer
      | Cloudflare           commands, progress, preview, asset checks
      | GitHub               pinned source checkout in Colab
      + private ComfyUI      fixed workflow only
```

## Providers

`colab` is the default requested backend. It cannot become ready unless the
official CLI, an actual A100 probe, licensed geometry assets, the managed
Cloudflare route, and Access credentials are all configured. `local-preview`
remains an explicit development provider and is never used as evidence that a
Colab render succeeded.

## Production adapters

The production geometry adapter evaluates canonical GNM v3 head geometry and
SMPL-X in an isolated Python 3.10+ worker, exports distinct `gnm_head`,
`neck_connector`, and `smplx_body` meshes in one aligned GLB, and projects the
real SMPL-X joints. The main viewport composes all three meshes. The modal GNM
Expression Studio isolates `gnm_head` for facial direction without claiming
that GNM and SMPL-X share or can be remeshed into one topology. SMPL-X owns
global head orientation; GNM contributes identity, local expression, and gaze.

The production runtime adapter must:

1. provision or reconnect through Colab CLI;
2. clone or fetch the GitHub source repository into
   `/content/formframe/source` at the configured commit revision;
3. restore pinned model assets;
4. bind ComfyUI to `127.0.0.1:8188`;
5. expose only the FormFrame gateway at `127.0.0.1:8000`;
6. validate Cloudflare Access JWT issuer and audience;
7. accept only Conditioning Contract v1 bundles and whitelisted parameters;
8. run a real fixed-workflow warmup before reporting ready;
9. fall back to CLI submission when the live control channel fails.

Transfer routing is measured per active session. Bulk payloads stay on Colab
CLI: bootstrap secrets, `.ffjob` bundles, reusable asset-cache misses, and final
PNG downloads. Licensed GNM and SMPL-X files remain local; Colab receives only
their derived conditioning images. Cloudflare carries authenticated control,
status/progress, preview delivery, benchmark echo checks, and content-addressed
cache checks. GitHub is the source of truth for runtime code; credentials are
passed only through runtime secrets/environment. The local Mac cache records
source-revision and model-manifest integrity metadata; it does not mirror model
weights locally.

## Project persistence

Each saved project is a directory:

```text
project_id.ffproject/
├── project.json
├── character.json
├── scene.json
├── references/
├── proxies/
├── thumbnails/
├── renders/
└── history.sqlite
```

Generated jobs live in the controller data root and are referenced from project history. A remote runtime is never the source of truth.

## Security boundary

No endpoint accepts shell commands or workflow JSON. Asset uploads are hash-verified and size-limited. Result responses use `Cache-Control: no-store`. Secrets belong in a later Keychain-backed configuration adapter, never in browser JavaScript.
