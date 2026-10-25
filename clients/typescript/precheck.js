// Pra-cek foto di HP/browser SEBELUM dikirim ke engine (tanpa server, tanpa AI, tanpa dependency).
//
// Tujuan: kurir langsung tahu foto buram/gelap/terlalu terang/kecil dan bisa mengulang saat itu juga, sehingga
// lebih sedikit foto yang ditolak engine. Rumus ketajaman sama dengan engine (variance of Laplacian per tile pada
// sisi panjang 1024 px, skala log 4..120), jadi skornya sejalan dengan skor `sharpness` engine.
//
// Hanya saran: keputusan akhir tetap dari engine (`checkFile`). Jangan memblokir kurir hanya dari pra-cek.
//
// Pemakaian (browser / WebView):
//   import { precheckImage } from "./precheck.js";
//   const pc = await precheckImage(file);          // file dari <input type="file" capture>
//   if (!pc.ok) tampilkan(pc.issues.map(i => i.message).join("\n"));  // tawarkan "Foto ulang" / "Tetap kirim"
//
// File ini juga disajikan engine di /ui/precheck.js (salinan identik, dicek oleh test).

const LONG_SIDE = 1024;
const GRID = 4;
const LOW = 4;
const HIGH = 120;

/** Batas bawaan, sama dengan profile `default` engine (reject sharpness 25, resolusi ideal sisi pendek 1080). */
export const DEFAULT_LIMITS = { blurScore: 25, darkMean: 45, brightClip: 0.5, minShortSide: 720 };

const MESSAGES = {
  BLURRY: "Foto buram. Pegang HP dengan stabil, ketuk layar untuk fokus, lalu foto ulang.",
  TOO_DARK: "Foto terlalu gelap. Cari tempat lebih terang atau nyalakan lampu kilat.",
  TOO_BRIGHT: "Foto terlalu terang/silau. Hindari cahaya langsung ke kamera.",
  LOW_RESOLUTION: "Resolusi foto terlalu kecil. Pakai kamera HP langsung, bukan tangkapan layar.",
};

function blur3(src, w, h) {
  // Gaussian 3x3 (1 2 1), sama dengan cv2.GaussianBlur((3,3), 0)
  const tmp = new Float32Array(w * h);
  const out = new Float32Array(w * h);
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const l = src[y * w + Math.max(0, x - 1)], c = src[y * w + x], r = src[y * w + Math.min(w - 1, x + 1)];
      tmp[y * w + x] = (l + 2 * c + r) / 4;
    }
  }
  for (let y = 0; y < h; y++) {
    const up = Math.max(0, y - 1) * w, mid = y * w, dn = Math.min(h - 1, y + 1) * w;
    for (let x = 0; x < w; x++) out[mid + x] = (tmp[up + x] + 2 * tmp[mid + x] + tmp[dn + x]) / 4;
  }
  return out;
}

/**
 * Nilai foto dari piksel grayscale (0-255). Bisa dipakai di Node untuk test.
 * @param {ArrayLike<number>} gray  @param {number} w  @param {number} h
 * @param {{width?: number, height?: number}} [original] ukuran foto asli (untuk cek resolusi)
 */
export function assessGray(gray, w, h, original = {}, limits = DEFAULT_LIMITS) {
  let sum = 0, sq = 0, dark = 0, bright = 0;
  for (let i = 0; i < w * h; i++) {
    const v = gray[i];
    sum += v; sq += v * v;
    if (v < 20) dark++;
    if (v > 250) bright++;
  }
  const n = w * h, mean = sum / n, std = Math.sqrt(Math.max(0, sq / n - mean * mean));
  const gain = Math.min(4, Math.max(1, 50 / Math.max(std, 1e-3)));
  const s = blur3(gray, w, h);
  const tiles = [];
  for (let i = 0; i < GRID; i++) {
    for (let j = 0; j < GRID; j++) {
      const y0 = Math.floor((i * h) / GRID), y1 = Math.floor(((i + 1) * h) / GRID);
      const x0 = Math.floor((j * w) / GRID), x1 = Math.floor(((j + 1) * w) / GRID);
      let a = 0, b = 0, m = 0;
      for (let y = Math.max(1, y0); y < Math.min(h - 1, y1); y++) {
        for (let x = Math.max(1, x0); x < Math.min(w - 1, x1); x++) {
          const k = y * w + x;
          const lap = s[k - 1] + s[k + 1] + s[k - w] + s[k + w] - 4 * s[k];
          a += lap; b += lap * lap; m++;
        }
      }
      if (m) tiles.push((b / m - (a / m) ** 2) * gain * gain);
    }
  }
  tiles.sort((p, q) => p - q);
  const top = tiles.slice(-Math.max(1, Math.floor(tiles.length / 4)));
  const metric = top.reduce((p, q) => p + q, 0) / top.length;
  const lg = (v) => Math.log10(v + 1);
  const sharpness = Math.round(Math.min(1, Math.max(0, (lg(metric) - lg(LOW)) / (lg(HIGH) - lg(LOW)))) * 1000) / 10;
  const W = original.width || w, H = original.height || h;
  const issues = [];
  if (sharpness < limits.blurScore) issues.push("BLURRY");
  if (mean < limits.darkMean) issues.push("TOO_DARK");
  else if (bright / n > limits.brightClip) issues.push("TOO_BRIGHT");
  if (Math.min(W, H) < limits.minShortSide) issues.push("LOW_RESOLUTION");
  return {
    ok: issues.length === 0,
    issues: issues.map((code) => ({ code, message: MESSAGES[code] })),
    metrics: { sharpness, brightness: Math.round(mean), darkFraction: dark / n, brightFraction: bright / n,
      width: W, height: H },
  };
}

/**
 * Pra-cek file foto di browser. Tidak pernah throw: jika browser tidak mendukung, hasilnya ok=true (fail-open).
 * @param {Blob} file
 */
export async function precheckImage(file, limits = DEFAULT_LIMITS) {
  try {
    const bmp = await createImageBitmap(file, { imageOrientation: "from-image" });
    const scale = Math.min(1, LONG_SIDE / Math.max(bmp.width, bmp.height));
    const w = Math.max(1, Math.round(bmp.width * scale)), h = Math.max(1, Math.round(bmp.height * scale));
    const canvas = typeof OffscreenCanvas !== "undefined" ? new OffscreenCanvas(w, h)
      : Object.assign(document.createElement("canvas"), { width: w, height: h });
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(bmp, 0, 0, w, h);
    const rgba = ctx.getImageData(0, 0, w, h).data;
    const gray = new Uint8ClampedArray(w * h);
    for (let i = 0, j = 0; i < gray.length; i++, j += 4) {
      gray[i] = 0.299 * rgba[j] + 0.587 * rgba[j + 1] + 0.114 * rgba[j + 2];
    }
    const r = assessGray(gray, w, h, { width: bmp.width, height: bmp.height }, limits);
    bmp.close?.();
    return r;
  } catch (e) {
    return { ok: true, issues: [], metrics: null, error: String(e) };
  }
}
