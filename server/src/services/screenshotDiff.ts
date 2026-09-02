import sharp from "sharp";
import pixelmatch from "pixelmatch";
import type { BoundingBox } from "../types.js";

const DIFF_WIDTH = 800; // normalize both images to this size before comparing
const DIFF_HEIGHT = 450;

// Returns bounding boxes of changed regions, scaled back to 0-1 fractional
// coordinates so the frontend can position overlays regardless of display size
export async function diffScreenshots(
  oldPath: string,
  newPath: string,
): Promise<BoundingBox[]> {
  const [oldRaw, newRaw] = await Promise.all([
    sharp(oldPath)
      .resize(DIFF_WIDTH, DIFF_HEIGHT, { fit: "fill" })
      .ensureAlpha()
      .raw()
      .toBuffer(),
    sharp(newPath)
      .resize(DIFF_WIDTH, DIFF_HEIGHT, { fit: "fill" })
      .ensureAlpha()
      .raw()
      .toBuffer(),
  ]);

  const diffBuffer = Buffer.alloc(DIFF_WIDTH * DIFF_HEIGHT * 4);

  const diffPixelCount = pixelmatch(
    new Uint8Array(oldRaw.buffer, oldRaw.byteOffset, oldRaw.length),
    new Uint8Array(newRaw.buffer, newRaw.byteOffset, newRaw.length),
    new Uint8Array(diffBuffer.buffer, diffBuffer.byteOffset, diffBuffer.length),
    DIFF_WIDTH,
    DIFF_HEIGHT,
    { threshold: 0.1 },
  );

  if (diffPixelCount === 0) return [];

  return extractBoundingBoxes(diffBuffer, DIFF_WIDTH, DIFF_HEIGHT);
}

// Simple connected-region-free approach: find the overall bounding box of all
// differing pixels. Good enough for MVP — a UI change usually clusters in one area.
// (For multiple disjoint changed regions, a flood-fill/connected-components pass
// would be the next iteration.)
function extractBoundingBoxes(
  diffBuffer: Buffer,
  width: number,
  height: number,
): BoundingBox[] {
  let minX = width,
    minY = height,
    maxX = 0,
    maxY = 0;
  let found = false;

  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const idx = (y * width + x) * 4;
      const alpha = diffBuffer[idx + 3];
      if (alpha > 0) {
        found = true;
        if (x < minX) minX = x;
        if (x > maxX) maxX = x;
        if (y < minY) minY = y;
        if (y > maxY) maxY = y;
      }
    }
  }

  if (!found) return [];

  return [
    {
      x: minX / width,
      y: minY / height,
      width: (maxX - minX) / width,
      height: (maxY - minY) / height,
    },
  ];
}
