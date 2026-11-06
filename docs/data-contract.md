# FarmCheck data contract

## Interfaces

`source.load_catalog(mode: str, zip_path: Path | None = None) -> Catalog`
accepts `synthetic` or `green_tea`. The latter requires a server-owned ZIP path.
`Catalog.records` indexes frozen `SourceRecord` models by ID.
`Catalog.image_bytes(record_id) -> tuple[bytes, str]` reads the catalog's paired
JPEG or returns FarmCheck's owned synthetic SVG. No client-selected path is read.
The loader never extracts, changes or publishes the source archive.

`SourceRecord` contains `id`, `origin`, `dataset_id`, `member_path`, `raw_hash`,
`source_group_id`, `raw_json`, `crop`, `parsed_fields`, `photo_time`, `env_time`,
`actual_dimensions`, `declared_dimensions`, `image_member_path` and `scenario`.
`raw_bytes` returns the exact original UTF-8 bytes; `raw_hash` is their SHA256.
Identity is SHA256 of dataset ID, NUL, member path, NUL and raw hash.
`raw` returns a freshly decoded tree with Decimal numeric tokens.
`values` returns a disposable dictionary of frozen `ParsedValue` instances.
Mutating either returned dictionary cannot mutate source evidence.
`reference(field_path) -> SourceReference` preserves record ID, member path,
raw hash, field path, source group and origin. Caption repetitions and later
rephrasings refer back to this same source group; they are not independent proof.

Pydantic models are frozen and reject extra input fields. For persistence,
serialize source records using
`record.model_dump_json(exclude={"raw", "values"})`, then deserialize with
`SourceRecord.model_validate_json`. Computed presentation fields are excluded
from roundtrip input. Decimal values serialize as strings, not binary floats.
The exact source JSON text remains authoritative for original token types.

`rules.assess(record, overlays: dict[str, str], model: ModelComparison | None)`
returns a frozen `Assessment`: record ID, rule version, checks, candidate flag,
original group ID and effective parsed fields. `effective_values` is its
disposable dictionary index. For assessment roundtrip persistence exclude
`effective_values`. Overlays do not modify the original record.

`RuleResult` contains rule ID/version, `matched | not_matched | not_evaluable`,
field paths, evidence references, explanation and next question.
`RULE_REGISTRY` records purpose, assumed unit, limits and next question.
`ModelComparison` contains `available | failed | stale | not_adopted`, run ID,
origin, optional crop/week and confidence. Only an available, adopted, current
result with both crop and week participates in comparison.
Confidence is model output, not calibrated correctness or source confirmation.

## Exact values, units and clocks

`normalize.parse_number(raw, field_path, *, present=True)` accepts source JSON
scalars, retaining absent versus null presence. Status is `missing`, `blank`,
`dash`, `invalid`, `zero` or `number`. Surrounding whitespace alone is removed
for interpretation. ASCII decimal and exponent syntax is accepted. Booleans,
NaN, Infinity, commas, Unicode digits and unrepresentable exponents are invalid.
`-0` remains a raw token and parses as numeric zero.
JSON numeric tokens are decoded directly to Decimal, avoiding float roundtrips.
For example, `14.0000000000000001` remains greater than 14.

Required fields are `environment.ph`, `.ec`, `.co2`, `.temp`, `.humidity` and
`growth.growth_cycle`. The source week is **growth.growth_cycle**, not a filename
suffix. Optional `growth.root_length = "-"` does not create a required error.

`raw_unit`, `unit_source_ref` and `unit_status` are independent of numbers.
Only unit symbols present in the source caption are marked `source_declared`.
The caption is a declaration and same-source repetition, not independent unit
verification. Unknown units remain unknown. There is no EC conversion, decimal
shift, field swapping, optimal-range judgment, diagnosis or prescription.

Photo times use the supplied 2024 `%y%m%d%H%M%S` context; environment times use
`%Y-%m-%d %H:%M:%S`. Parsed datetimes deliberately remain naive source-local
clocks, with `timezone_status="unknown"`. No timezone is invented.
`normalize.time_difference(record)` reports signed environment-minus-photo
wall-clock seconds labelled `기록된 시각 차이·시간대 미확인`, or null with a reason.
A positive difference is informational and never becomes a future-time error.

## Grouping and comparisons

`group_identity(record)` uses original dataset/crop/origin, exact parsed
environment timestamp, canonical Decimal pH/EC/CO2/temperature and each unit's
interpretation. Canonicalization never rounds through the Decimal context.
Consistent unknown-unit bases can group, but remain qualified as unknown.
Different declared bases do not group. Missing or invalid key values become
singletons. Grouping is a presentation relation, never record deletion, a
sensor-measurement count, or propagation of human resolution.

Corrections affect current rule outcomes but never original group membership.
The original 33 records / 24 groups remain a separate baseline from current
matching-record/group counts.
`peers(record, records, overlays)` returns at most ten same-crop/week/unit-basis
original peers, ordered by photo timestamp then ID. It makes no individual
growth-trend claim. The service owns selection using other records' effective
overlays when they have been corrected.

Screens are strict pH > 14; required numeric interpretability; and adopted
model-versus-effective-record crop/week difference. pH 14 does not match.
Invalid pH is not evaluable for that screen and matches required-numeric review.
Failed, stale, absent and unadopted model output is not evaluable.
Unknown source units and dataset image-size mismatch are context, not diagnoses.
Image dimensions are metadata only; label coordinates are never transformed.

## Owned synthetic cases

Every public image is an owned SVG marked `SYNTHETIC DEMO`.
Every synthetic record has a scenario and synthetic dataset ID.
Scenarios are shared candidate A/B, exact pH 14, immediately above 14,
injected week difference, model failure, missing required value and numeric -0.
The optional `synthetic_model(record)` fixture returns explicitly **injected**
week 4 versus source week 3, or an explicitly injected failure. These are not
live model calls and cannot count toward actual inference evidence.
Actual ZIP records never receive these fixtures.

## Actual inventory evidence (2026-11-06)

Command:

```text
uv run python -m farmcheck.source --zip C:/Users/user/Downloads/Sample.zip --crop 녹차
```

The CLI also accepts `--report PATH` to write aggregated inventory only.
No original records, images or coordinates are included in this public summary.

| Measurement | Observed |
|---|---:|
| ZIP bytes | 510148215 |
| ZIP JSON files | 25602 |
| ZIP JPEG files | 25602 |
| Green-tea image/JSON pairs | 3872 |
| Original pH > 14 photo records | 33 |
| Original five-field environment candidate groups | 24 |
| Actual green-tea JPEG header size 380 x 380 | 3872 |
| Declared 1920 x 1080 | 250 |
| Declared 3840 x 2160 | 3591 |
| Declared 4000 x 2252 | 31 |
| Environment clock later than photo, any positive delta | 540 |
| Environment clock later by 1-10 seconds | 148 |

ZIP SHA256:
`5916985fa1a9047528d9a43d99a60398067546ce43eef26415b0ffe20d1d9c1c`

The 148 figure is the small-positive subset, not all later environment times.
JPEG dimensions came from Pillow header reads of all 3872 paired green-tea
images; this is not a claim of full pixel decoding or image-model accuracy.
The 24 groups directly recompute timestamp + pH + EC + CO2 + temperature,
with the declared unit bases retained. They do not establish 24 independent
sensor failures, plant problems or physical events.

## Verification receipt

Executed in the shared build worktree with the supplied `uv.exe`:

```text
uv run ruff check src/farmcheck/contracts.py src/farmcheck/normalize.py src/farmcheck/source.py src/farmcheck/rules.py tests/test_source.py tests/test_normalize.py tests/test_rules.py
uv run basedpyright src/farmcheck/contracts.py src/farmcheck/normalize.py src/farmcheck/source.py src/farmcheck/rules.py tests/test_source.py tests/test_normalize.py tests/test_rules.py
uv run pytest -q tests/test_source.py tests/test_normalize.py tests/test_rules.py
```

All three commands exited 0: Ruff passed, basedpyright reported 0 errors /
0 warnings / 0 notes, and pytest reported **32 passed in 0.22s**.
Coverage includes exact raw JSON decimal tokens, Decimal roundtrip, typed
configuration errors, absent/null/blank/dash/zero/invalid input, oversized
exponents, timestamp context, positive clock deltas, source-byte preservation,
private ZIP/image pairing, dimensions, pH boundary, stable corrected membership,
qualified unknown units, injected mismatch and failed/unadopted model output.
The inventory CLI separately exited 0 against the supplied original ZIP.

The editor language-server tool could not find `basedpyright-langserver` on its
own PATH. The project-local `uv run basedpyright` command above did run cleanly;
no dependency or manifest changes were made to work around the editor tool.

## Review corrections

Malformed JSON, missing paired images and unreadable image headers are retained
as `Catalog.problems`: immutable `SourceProblem` entries with member path,
exact byte hash, problem kind and message. Other records continue loading.
`Inventory.source_problems` counts these separately from valid photo records;
bootstrap and the UI expose them rather than silently dropping them.
The supplied archive has zero source problems.

Group detail includes each field's unit status, raw unit and source reference.
Peer unit status remains unknown when any compared unit is unknown.
Resolution evidence must cover the currently matched checks; model disagreement
also requires a model-run reference. Model references retain `kind="model"` and
their actual/injected origin instead of being labelled original JSON.
