# Green-tea growth-week transfer-learning pilot

This is an experimental training path, separate from the public FarmCheck app.
The product-direction discussion has not changed the authorized model target:
estimate recorded green-tea growth week from image pixels.

## Source and evaluation limits

The supplied archive contains 3,872 green-tea images. Weeks 9-11 are absent.
Plant/pot/cultivation-cycle identifiers are unavailable, and background, view
and photographer can correlate with week. Capture-day separation is the
minimum diagnostic grouping, not proof of physical or scene independence.

The existing platform checkpoint also comes from AI Hub dataset 71853.
Its original training split is unknown. The pilot test set may therefore have
been seen during pretraining. Scores must be described as same-source
diagnostic results, never as independent accuracy or farm effectiveness.

The fixed policy uses weeks 1, 3, 4, 7, 8, 12 and 13:

| Partition | Images | Purpose |
| --- | ---: | --- |
| Train | 1,971 | Fit classifier parameters |
| Validation | 805 | Choose the trained checkpoint |
| Test | 1,033 | One final comparison after selection |
| Quarantine | 63 | Preserve unsupported source weeks without training/evaluation |

Policy and source hashes are stored in the private manifest. No image, caption,
source archive, model checkpoint or private manifest belongs in Git.
The prepared JPEG bytes are unchanged; training transforms do not rewrite them.
Only pixels enter the model. Filenames, dates, source labels and environmental
measurements are not input features.

## Existing model reuse

The platform model URI is `models:/model-dbfcfb0b/1`.
The downloaded bundle includes crop classification plus nine crop-specific
growth models. `05.green_tea.h5` is a weights-only TensorFlow/Keras 2.4 file,
with a 1,280-by-14 final classifier kernel and 14 biases.

Before training, the loader must account for every learned array and demonstrate
numerical agreement with a compatible reference. Ordinary PyTorch EfficientNet
padding must not be assumed equivalent to TensorFlow SAME padding.
Do not silently skip mismatched layers or substitute ImageNet weights.

Preserve the 14-output architecture. The seven-label experiment does not validate
the remaining output classes. Inverse H5 export must be checked by reloading;
all other crop files remain byte-identical when packaging a new version.

## Predeclared first experiment

These settings are a plan, not a claim that training has already run.

- Seed: 20261107.
- First adaptation: freeze the feature extractor and train the final classifier.
- Extract deterministic float32 features with the verified original
  preprocessing. No label-dependent transforms or test-time tuning.
- Optimizer: AdamW; learning rate 0.001; weight decay 0.0001; batch size 64.
- Loss: cross entropy with inverse-square-root class-frequency weights computed
  only from the supported training labels and normalized to mean one.
- Maximum 20 epochs; stop after five epochs without validation improvement.
- Choose the best **trained** epoch by validation macro-F1 over the seven
  supported labels, then lower week MAE as the tie-breaker.
- Record the unchanged pretrained baseline separately. Do not describe epoch 0
  as a newly trained model if adaptation fails to improve it.
- Evaluate the selected model and original checkpoint on the fixed test split
  only after checkpoint selection is finished.

Record accuracy, supported-label macro-F1, balanced accuracy, numeric week MAE,
per-week counts/precision/recall, a 14-by-14 confusion matrix, and the fraction
of predictions outside the seven evaluated labels. Unsupported predictions
remain errors; do not remove them from the denominator.

Also report constant-majority and constant-median predictors fitted only on
training labels. Preserve per-record predictions and representative errors in
private outputs. Small early-week counts and six test days limit uncertainty
claims; image counts alone do not establish independent sample size.

## Deployment decision

Create a separate experimental registered model and serving resource if the
artifact passes loading and API-contract checks. A successful deployment is
not evidence of improved predictive performance.

Keep the existing public FarmCheck endpoint and shared education model intact.
If the adapted model is worse than the unchanged checkpoint, report that
explicitly and do not promote it as the default model.

AIStream workflow cloning retains the original serving output binding. The
new workflow must explicitly choose **new serving**, not a new version of the
existing shared serving. Pin the model version and serving-template version,
use one CPU serving replica with the required 4 CPU / 8GiB profile and no GPU,
and inspect the platform's idle-stop/cost controls before execution.
