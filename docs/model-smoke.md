# Crop gateway and private connectivity smoke

## Interface

```python
from farmcheck.cropclient import CropClient, Prediction

prediction: Prediction = CropClient().predict(image_bytes, source_filename)
```

`Prediction` is the frozen union `PredictionSuccess | PredictionFailure`.
Both carry `run_id`, `status`, `http_status`, and `raw_response`.
Success has `status="success"`, `width`, `height`, `crop` and `growth_week`.
`crop` contains `class_index`, `name_ko`, `name_en`, and `confidence`;
`growth_week` contains `class_index`, `label`, `confidence`, and `num_classes`.
Failure has `status="failed"`, `error`, and optional `retry_after`.
No result adopts a label, updates review state, or resolves a candidate.
Callers own record/version binding and stale-response handling.

The client accepts no URL. Its only network destination is:

```text
POST https://aiopen-serving.jntp-ax.or.kr/jntp-cpu/SV-c562094f/predict
```

One multipart field, `file`, contains the original image bytes. The caller's filename
is discarded; the upload name is `<run_id>.<detected-format-extension>`.
Source JSON, crop/week labels, captions, record identifiers, and paths are not sent.
Tests inspect the actual serialized multipart request.

JPEG, PNG, WEBP, and BMP are accepted only as single-frame images, up to 10 MiB
and 16,000,000 pixels. Local rejection makes no HTTP call.
Connect/read/write/pool timeouts are 5/60/15/5 seconds; responses are capped at
1 MiB. Redirects, environment proxy settings, automatic retries, and configurable
origins are disabled. Each call owns and closes its synchronous httpx2 client.
HTTP failures, including 429, preserve status, bounded raw body, and Retry-After;
timeouts and transport failures have no invented HTTP status.
Malformed JSON, invalid confidence/class bounds, inconsistent dimensions or week
class count/label fail explicitly rather than produce success.
Full vendor probability arrays remain in `raw_response`, not mutable result fields.

## Official schema verification

The official `/openapi.json` was fetched directly on November 6, 2026.
It reports application version `0.4.0`, OpenAPI `3.1.0`, nine crop classes,
14 green-tea week classes, and the limits above.
The private exact schema copy is `artifacts/private/model-openapi.json`;
its SHA-256 is
`8a822f2bb8f5f521f1290a9116b5dc6fedf2196878dd955e3ac68a307a4e93db`.
Schema URL:

```text
https://aiopen-serving.jntp-ax.or.kr/jntp-cpu/SV-c562094f/openapi.json
```

## Actual five-image run

```console
uv run python scripts/model_smoke.py --zip C:/Users/user/Downloads/Sample.zip --crop 녹차 --count 5 --output artifacts/private/model-smoke.json
```

The actual run began at `2026-11-06T17:31:27.339624+00:00` and finished at
`2026-11-06T17:31:30.661110+00:00`.
Selection uses parsed `growth.growth_cycle`, not the trailing filename code.
For each of the five lowest available distinct weeks it selects the
lexicographically first green-tea JSON member and its paired original image.
Available weeks were `0,1,2,3,4,5,6,7,8,12,13`.
All five images were 380 by 380 pixels and were uploaded unchanged.

| Source week | HTTP | Returned crop | Returned week | Week confidence |
| --- | --- | --- | --- | --- |
| 0 | 200 | 녹차 | 0 | 0.944381 |
| 1 | 200 | 녹차 | 1 | 1.0 |
| 2 | 200 | 녹차 | 2 | 0.922187 |
| 3 | 200 | 녹차 | 3 | 1.0 |
| 4 | 200 | 녹차 | 4 | 1.0 |

The private report records exact run IDs/opaque upload filenames, source member
references, original image SHA-256 and byte lengths, endpoint/method/field names,
and complete raw vendor responses. It contains no embedded images.
Do not publish that report, the source ZIP, or the real images.
This document intentionally contains only a redacted summary.

These five HTTP 200 responses demonstrate connectivity and response parsing.
Their agreement with five source records is **not an accuracy estimate**:
the small deterministic convenience sample is not independent ground truth,
training overlap is unknown, and vendor confidence is not calibrated correctness.
There was no field-effect, crop-health, diagnosis, or agent-performance experiment.

## Deterministic regression coverage

`tests/test_cropclient.py` uses real generated image bytes and `httpx2.MockTransport`.
It checks an opaque multipart upload, HTTP 413/422/429/500/redirect rejection,
timeout/connect failure, malformed and inconsistent success replies, empty/corrupt/
oversized images, the pixel bound, animation rejection, and the response-size cap.
No sleeps, remote calls, retry loops, or fake successful vendor smoke are used.

The verified local test run passed all 20 cases in 0.36 seconds. Targeted Ruff
checks passed, and targeted basedpyright reported zero errors and zero warnings
for the client, tests, and smoke runner. The language-server executable was not
on PATH; the installed project type checker was invoked directly through `uv`.
The three Python files are below 200 nonblank, noncomment lines each.

## Additional local application integration

After the five-image smoke, the lead sent one additional actual green-tea record
through the local application's `/classify` route. It returned HTTP 200, green
tea/week4, workflow revision2 and data revision1, with `needs_review` and `open`.
It did not mark human review complete. The response receipt endpoint returned
one immutable run. A human hold, explicit recheck and selected-record handover
then each returned200, with the unresolved held record retained.

This is one application integration check, not a sixth sample added to an
accuracy claim. Its original-response evidence remains private in the local
V05 evidence directory. No actual source image or raw source record was added
to the public app or repository.
