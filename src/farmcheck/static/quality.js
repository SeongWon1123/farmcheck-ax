// Copyright (c) 2026 FarmCheck contributors.
// Optional review hints only. Calibration: docs/quality-checks-eval.md (2026-11-07).
(() => {
  "use strict";

  // 400 original tea images: minimum Laplacian variance 98.7305.
  // All 1,200 Gaussian-blurred copies (r=1.5/3/5) were below 24.6742.
  const BLUR_SHARPNESS_THRESHOLD = 90;
  // Original brightness 112.5806..213.0566; dark x0.35: 400/400 below 110.
  const DARK_BRIGHTNESS_THRESHOLD = 110;
  // Bright x1.8: 390/400 above 220; no originals above 220.
  const BRIGHT_BRIGHTNESS_THRESHOLD = 220;
  // Original contrast minimum 17.6902; contrast x0.2: 400/400 below 17.
  const LOW_CONTRAST_THRESHOLD = 17;
  // 20/20 tea confidences = 1; two stress cases = 0.699493/0.682859.
  // 0.8 is a demo review cutoff, not a calibrated probability of label error.
  const CROP_CONFIDENCE_THRESHOLD = 0.8;
  const MAX_IMAGE_EDGE = 380;

  /**
   * @typedef {Readonly<{sharpness:number, brightness:number, contrast:number}>} Metrics
   * @typedef {Readonly<{crop:Readonly<{name_ko:string, confidence:number}>}>} ModelResult
   * @typedef {Readonly<{code:string, level:"warn"|"info", message_ko:string,
   * value:number|string, threshold:number|string}>} Warning
   */

  /**
   * Measure a decoded image; call after img.decode()/load or createImageBitmap().
   * Integer BT.601 luma; four-neighbor 3x3 Laplacian, excluding the outer border.
   * All variances are population variances. RGB pixels are not uploaded.
   * @param {ImageBitmap|HTMLImageElement} image
   * @returns {Metrics}
   */
  function measure(image) {
    const sourceWidth = "naturalWidth" in image ? image.naturalWidth : image.width;
    const sourceHeight = "naturalHeight" in image ? image.naturalHeight : image.height;
    if (sourceWidth < 1 || sourceHeight < 1) {
      throw new RangeError("Photo must be decoded before quality measurement.");
    }
    const scale = Math.min(1, MAX_IMAGE_EDGE / Math.max(sourceWidth, sourceHeight));
    const width = Math.max(1, Math.round(sourceWidth * scale));
    const height = Math.max(1, Math.round(sourceHeight * scale));
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext("2d", { willReadFrequently: true });
    if (!context) {
      throw new DOMException("Canvas 2D is unavailable.", "NotSupportedError");
    }
    context.drawImage(image, 0, 0, width, height);
    const pixels = context.getImageData(0, 0, width, height).data;
    const luma = new Uint8Array(width * height);
    let sum = 0;
    let squares = 0;
    for (let i = 0; i < luma.length; i++) {
      const offset = i * 4;
      const value = Math.floor(
        (299 * pixels[offset] + 587 * pixels[offset + 1] + 114 * pixels[offset + 2] + 500) / 1000,
      );
      luma[i] = value;
      sum += value;
      squares += value * value;
    }
    let lapSum = 0;
    let lapSquares = 0;
    for (let y = 1; y < height - 1; y++) {
      for (let x = 1; x < width - 1; x++) {
        const i = y * width + x;
        const lap = luma[i - width] + luma[i - 1] - 4 * luma[i]
          + luma[i + 1] + luma[i + width];
        lapSum += lap;
        lapSquares += lap * lap;
      }
    }
    const interior = Math.max(0, width - 2) * Math.max(0, height - 2);
    const brightness = sum / luma.length;
    return {
      sharpness: interior ? Math.max(0, lapSquares / interior - (lapSum / interior) ** 2) : 0,
      brightness,
      contrast: Math.sqrt(Math.max(0, squares / luma.length - brightness ** 2)),
    };
  }

  /**
   * Raw model API shape, not the flattened detail.model object; see integration notes.
   * A warning never changes source annotations or the user's review decision.
   * @param {Metrics} metrics
   * @param {ModelResult} [modelResult]
   * @returns {Warning[]}
   */
  function assess(metrics, modelResult) {
    /** @type {Warning[]} */
    const warnings = [];
    if (metrics.sharpness < BLUR_SHARPNESS_THRESHOLD) {
      warnings.push({
        code: "BLURRY", level: "warn", message_ko: "사진의 선명도 확인 권장: 초점과 흔들림을 살펴보세요.",
        value: metrics.sharpness, threshold: BLUR_SHARPNESS_THRESHOLD,
      });
    }
    if (metrics.brightness < DARK_BRIGHTNESS_THRESHOLD) {
      warnings.push({
        code: "TOO_DARK", level: "warn", message_ko: "사진의 밝기 확인 권장: 어두운 부분의 작물이 잘 보이는지 살펴보세요.",
        value: metrics.brightness, threshold: DARK_BRIGHTNESS_THRESHOLD,
      });
    }
    if (metrics.brightness > BRIGHT_BRIGHTNESS_THRESHOLD) {
      warnings.push({
        code: "TOO_BRIGHT", level: "warn", message_ko: "사진의 밝기 확인 권장: 밝은 부분의 세부 정보가 보이는지 살펴보세요.",
        value: metrics.brightness, threshold: BRIGHT_BRIGHTNESS_THRESHOLD,
      });
    }
    if (metrics.contrast < LOW_CONTRAST_THRESHOLD) {
      warnings.push({
        code: "LOW_CONTRAST", level: "warn", message_ko: "사진의 대비 확인 권장: 작물과 배경이 구분되는지 살펴보세요.",
        value: metrics.contrast, threshold: LOW_CONTRAST_THRESHOLD,
      });
    }
    if (modelResult) {
      const crop = modelResult.crop;
      if (crop.name_ko !== "녹차") {
        warnings.push({
          code: "CROP_NOT_TEA", level: "info",
          message_ko: "작물 정보 확인 권장: 모델의 녹차 이외 분류 결과를 사진과 함께 검토하세요.",
          value: crop.name_ko, threshold: "녹차",
        });
      } else if (crop.confidence < CROP_CONFIDENCE_THRESHOLD) {
        warnings.push({
          code: "CROP_LOW_CONFIDENCE", level: "info",
          message_ko: "작물 정보 확인 권장: 녹차 분류 신뢰도가 낮아 사진과 기록을 함께 검토하세요.",
          value: crop.confidence, threshold: CROP_CONFIDENCE_THRESHOLD,
        });
      }
    }
    return warnings;
  }

  window.FarmQuality = Object.freeze({ measure, assess });
})();
