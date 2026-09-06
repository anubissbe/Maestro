# LoRA suggestions and automatic selection

These features are currently under [Unreleased](../CHANGELOG.md#unreleased).

## Where selection and keywords happen

| Workflow | How a LoRA is selected | When its keywords are added |
| --- | --- | --- |
| Studio, including Images | Click **Suggest LoRAs from prompt + image**, then **Add LoRA + keywords** | Immediately to the Studio prompt when applying the suggestion |
| Director Image/Video LoRA picker | Apply a suggestion in the corresponding selector | To existing image or video shot prompts; future pipeline planning uses the selected LoRAs' metadata |
| Director automatic selection | Enable **Automatically select compatible downloaded LoRAs** before new pipeline planning | During planning, to the chosen model's render prompts, including string keyframe/window prompts |
| Studio manual LoRA checkbox | Select the LoRA yourself | The checkbox and **Generate** do not automatically insert trigger words |

## Manual suggestions

1. Choose your generation model and enter a prompt, attach a reference image, or
   supply both. In Director, enter the scene description and reference images;
   existing shot prompts are also included in the assessment.
2. Open the LoRA selector. Director has separate **Image LoRAs** and **Video LoRAs**
   sections, each using its own model and active selection.
3. Click **Suggest LoRAs from prompt + image**. The LLM returns up to three
   alternatives, each with a reason and any locally recorded trigger words.
4. Click **Add LoRA + keywords** to apply one. Missing trigger phrases are appended
   without changing their spelling or repeating phrases already in the prompt.
5. To add another LoRA, request suggestions again. The new request considers the
   LoRA you just added. Multiple active LoRAs are supported; one addition per
   request prevents applying an unchecked combination of alternative suggestions.

Changing the model, prompt, reference images, or active LoRAs invalidates the
displayed suggestions. Reference images require a vision-capable configured LLM;
they must be valid image files under Maestro's uploads or configured outputs root.
Paths and symlinks escaping those directories are rejected before LLM submission.
The interactive request accepts at most eight images and 16,000 prompt characters.
Interactive requests may be deferred with an error while generation is using the GPU.

Suggestions use actual files from the model's existing Maestro LoRA catalog,
including resolved linked-library files. They do not download new LoRAs. A managed
adapter offered for first-use download is not a downloaded candidate until its
file exists. Mature-mode visibility is respected.

## AI advice and duplicate checks

An LLM's interaction assessment is not a loader compatibility test. Similar styles,
multiple identities, or missing metadata do not establish a technical conflict.
The selector displays these assessments as **AI advice**, and you can still apply
the suggestion manually.

Already active filenames are excluded. A candidate sharing an active LoRA's
catalog identifier is treated as another version and blocked by the manual
suggestion flow. This is a metadata identity check, not a content-hash comparison.
Compatibility still depends on correct library organization and metadata; visual
interactions between LoRAs are not guaranteed by this feature.

## Automatic Director selection

**Automatically select compatible downloaded LoRAs** is enabled for new Director
projects and stored in the project's settings snapshot. Older saved projects that
lack the setting reopen with it disabled.

During a new backend pipeline planning pass, after the GPU wait and LLM setup,
Director assesses the scene description and reference images against the local
catalog for each participating model. It may add **at most one image LoRA and one
video LoRA**. Each addition is checked against that model's existing selection.
Existing weight schedules are preserved; the addition uses available recommended
weights, including per-phase recommendations, or a default of 0.8.

The LLM is instructed to choose only a match useful throughout the project, and
to avoid acceleration, distillation, and workflow-changing adapters. Managed
adapters are excluded from automatic candidates. Video selection is skipped for
the MiniMax cloud video engine and models with LoRAs disabled; image selection is
skipped when the pipeline is not generating shot images.

Automatic selection skips AI warnings, incomplete assessments, and duplicate
identifiers. It can legitimately add nothing. The Director panel reports the
addition and reason, or that no suitable additional LoRA was found.

Choices, weights, and reasons are saved with the run. Repeated planning after a
partial restart reuses completed selections. Resuming saved clip plans or rendering
prepared, reviewed plans does not perform a fresh automatic selection. Editing the
checkbox does not change an already queued or running revision.

## Keyword source and limits

Keywords come from the LoRA's local `trainedWords` metadata, not guesses generated
from the filename or guide prose. Missing metadata means no keywords can be added.
Image and video keyword application is kept separate. Director's existing prompt
planning/polishing can also incorporate selected LoRA metadata, but that is distinct
from Studio's explicit **Add LoRA + keywords** action.

Studio currently has no automatic LoRA selection on **Generate**, no automatic
keyword insertion for manually checked LoRAs, and no multi-select application of
one suggestion response.

## Local prompt improver troubleshooting

If the local improver tries to download a path such as
`MiniMax-M3/resolve/main/MiniMax-M3-Q4_K_S.gguf`, the provider and model selections
do not match. MiniMax M-series model IDs belong to the MiniMax provider.

Under **Settings → Services**, select **Local (llama-server)** and a local GGUF
model. For reference-image analysis, choose a model with its vision projector
available. Saving affected settings now repairs the stale MiniMax selection;
future provider switches restore that provider's remembered model. A dedicated
local prompt enhancer is retained when changing the main provider.

The local loader also rejects a MiniMax M-series ID with an explicit settings
error rather than attempting a nonexistent GGUF download. This fix addresses a
provider/model mismatch; other download or authentication errors need their own
diagnosis.
