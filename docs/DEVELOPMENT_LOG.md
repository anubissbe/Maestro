# Development log

Track user-visible changes here with their date, validation, and current limits.
The [changelog](../CHANGELOG.md) summarizes release-facing changes; workflow
instructions belong in the linked guides. Entries describe the resulting behavior,
not a transcript of implementation attempts.

## 2026-09-06 — LoRA selection and local prompt improvement

Status: implemented in the working tree, documented under **Unreleased**.
The release version remains **2.0.1**.

### LoRA suggestions

- Added a server-filtered catalog recommendation service and shared Studio/Director
  suggestion UI, using prompts, reference images, metadata descriptions, guides,
  and trigger words. Generated filenames are checked against the catalog.
- Added one-at-a-time activation with missing-keyword insertion, stale-response
  invalidation, existing-selection checks, and metadata duplicate-version detection.
- Changed LLM uncertainty and interaction claims from blocking conflicts to manual
  advice. Automatic Director selection continues to skip advisory warnings.
- Restricted suggestion reference images to Maestro uploads and configured outputs;
  tests reject outside paths, traversal, escaping symlinks, and non-image files.
- Added automatic selection before new Director pipeline planning, bounded to one
  extra LoRA per model. Selection reports and weights are persisted; keywords go
  to the relevant image/video prompts. Existing reviewed/resumed plans are retained.

See [LoRA selection](LORA_SELECTION.md) for controls, duplicate-check semantics,
keyword behavior, and workflow limits.

### Local prompt improver

- Diagnosed Local retaining `MiniMax-M3` after a provider switch and attempting to
  download a nonexistent GGUF repository.
- Added remembered model IDs per provider, repair of affected settings on save,
  and an explicit local-loader mismatch error. Provider switches preserve a
  dedicated local enhancer; the settings model draft updates without a synchronous
  effect-driven state update.
- On the development installation, restored an already downloaded local Gemma
  model with its vision projector. This local configuration repair is not a
  shipped credentials or settings-file change.

### Validation recorded

- Targeted LoRA suggestion tests passed: catalog enforcement, missing files,
  duplicate IDs, advisory warnings, malformed responses, and keyword provenance.
- Director auto-selection tests passed: one addition per model, weight preservation,
  exclusion of managed/duplicate candidates, conservative warning handling,
  mode-specific keyword insertion, disabled models, and repeat-selection prevention.
- Keyword checks passed for case-insensitive duplicates, phrase boundaries, regex
  characters, whitespace, and Unicode. Provider-settings tests covered round trips,
  stale Local/MiniMax configuration, and explicit model choices.
- Existing Director project/queue and provider credential-resolution tests passed.
- UI production builds, targeted ESLint checks, Python syntax checks, and whitespace
  checks passed. Builds retained existing chunk-size and mixed-import warnings.
- A live local prompt-improver request with one reference image succeeded: 441
  characters returned in approximately 5.3 seconds, including model loading.
- A complete Director render using automatic LoRA selection has **not** been
  validated end to end. Do not interpret unit-test coverage as that live result.
- PR validation exposed an eager FastAPI import in the existing MiniMax helpers.
  FastAPI is now imported during HTTP route registration so the helper tests also
  run in the lightweight CPU CI environment without skipping those tests.

### Following changes

Add a dated entry for later behavior changes and update the relevant guide,
README feature summary, and **Unreleased** changelog entry together. Keep tests,
live validation, and known limitations explicit; do not record private prompts,
credentials, or machine-specific configuration contents.
