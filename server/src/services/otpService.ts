import crypto from "crypto";
import { prisma } from "../db/prisma.js";

const OTP_LENGTH = 6;
const EXPIRY_MINUTES = Number(process.env.OTP_EXPIRY_MINUTES ?? 10);

function generateOtpCode(): string {
  // cryptographically random digits, not Math.random() — this is a security-relevant value
  const buffer = crypto.randomInt(0, 10 ** OTP_LENGTH);
  return buffer.toString().padStart(OTP_LENGTH, "0");
}

export function isAllowedEmailDomain(email: string): boolean {
  const allowedEntries = (process.env.ALLOWED_EMAIL_DOMAIN ?? "")
    .toLowerCase()
    .split(",")
    .map((entry) => entry.trim())
    .filter(Boolean);
  const emailLc = email.toLowerCase();
  return allowedEntries.some((entry) => emailLc.endsWith(entry));
}

export async function createAndStoreOtp(email: string): Promise<string> {
  const code = generateOtpCode();
  const expiresAt = new Date(Date.now() + EXPIRY_MINUTES * 60 * 1000);

  // invalidate any previous unused codes for this email first
  await prisma.otpCode.updateMany({
    where: { email, used: false },
    data: { used: true },
  });

  await prisma.otpCode.create({ data: { email, code, expiresAt } });
  return code;
}

export async function verifyOtp(email: string, code: string): Promise<boolean> {
  const otpRecord = await prisma.otpCode.findFirst({
    where: { email, code, used: false, expiresAt: { gt: new Date() } },
    orderBy: { createdAt: "desc" },
  });

  if (!otpRecord) return false;

  await prisma.otpCode.update({
    where: { id: otpRecord.id },
    data: { used: true },
  });
  return true;
}
