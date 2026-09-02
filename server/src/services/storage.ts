import { v2 as cloudinary } from "cloudinary";
import path from "path";

cloudinary.config({
  cloud_name: process.env.CLOUDINARY_CLOUD_NAME,
  api_key: process.env.CLOUDINARY_API_KEY,
  api_secret: process.env.CLOUDINARY_API_SECRET,
});

type AssetType = "video" | "screenshots";

interface UploadJobContext {
  documentId: string;
  versionId: string;
  stepIndex?: number; // only relevant for screenshots
}

export async function uploadFileToCloudinary(
  localFilePath: string,
  assetType: AssetType,
  job: UploadJobContext,
  mimeType?: string, // pass req.file.mimetype for videos, omit for screenshots (always PNG)
): Promise<string> {
  const isVideo = mimeType
    ? mimeType.startsWith("video/")
    : [".mp4", ".mov", ".webm"].includes(
        path.extname(localFilePath).toLowerCase(),
      );

  const folder = `diff-engine/${assetType}/${job.documentId}/${job.versionId}`;
  const publicId =
    assetType === "video"
      ? "source"
      : `step-${String(job.stepIndex ?? 0).padStart(2, "0")}`;

  const result = await cloudinary.uploader.upload(localFilePath, {
    folder,
    public_id: publicId,
    resource_type: isVideo ? "video" : "image",
    overwrite: true,
  });

  return result.secure_url;
}

export async function downloadFromCloudinaryToTemp(
  url: string,
  tmpDir: string,
): Promise<string> {
  const fs = await import("fs");
  const { nanoid } = await import("nanoid");

  const res = await fetch(url);
  if (!res.ok)
    throw new Error(`Failed to download from Cloudinary: ${res.status}`);

  const buffer = Buffer.from(await res.arrayBuffer());
  const ext = path.extname(new URL(url).pathname) || ".png";
  const localPath = path.join(tmpDir, `${nanoid()}${ext}`);

  fs.mkdirSync(tmpDir, { recursive: true });
  fs.writeFileSync(localPath, buffer);

  return localPath;
}
