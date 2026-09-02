import { Router, type Request, type Response } from "express";
import jwt from "jsonwebtoken";
import {
  isAllowedEmailDomain,
  createAndStoreOtp,
  verifyOtp,
} from "../services/otpService.js";
import { sendOtpEmail } from "../services/email.js";

const router = Router();

const REQUEST_RATE_LIMIT_MS = 60_000; // one OTP request per email per minute
const recentRequestTimestamps = new Map<string, number>(); // simple in-memory limiter — fine for a demo, not for real scale

router.post("/request-otp", async (req: Request, res: Response) => {
  const { email } = req.body as { email?: string };

  if (!email || typeof email !== "string") {
    res.status(400).json({ error: "email is required" });
    return;
  }

  const normalizedEmail = email.trim().toLowerCase();

  if (!isAllowedEmailDomain(normalizedEmail)) {
    // deliberately vague — don't confirm/deny which domains are valid beyond this message
    res
      .status(403)
      .json({ error: "Please sign in with your company email address." });
    return;
  }

  const lastRequest = recentRequestTimestamps.get(normalizedEmail);
  if (lastRequest && Date.now() - lastRequest < REQUEST_RATE_LIMIT_MS) {
    res
      .status(429)
      .json({ error: "Please wait before requesting another code." });
    return;
  }

  try {
    const code = await createAndStoreOtp(normalizedEmail);
    await sendOtpEmail(normalizedEmail, code);
    recentRequestTimestamps.set(normalizedEmail, Date.now());
    res.status(200).json({ message: "Verification code sent." });
  } catch (err) {
    console.error("Failed to send OTP:", err);
    res.status(500).json({ error: "Failed to send verification code." });
  }
});

router.post("/verify-otp", async (req: Request, res: Response) => {
  const { email, code } = req.body as { email?: string; code?: string };

  if (!email || !code) {
    res.status(400).json({ error: "email and code are required" });
    return;
  }

  const normalizedEmail = email.trim().toLowerCase();
  const isValid = await verifyOtp(normalizedEmail, code.trim());

  if (!isValid) {
    res.status(401).json({ error: "Invalid or expired code." });
    return;
  }

  const token = jwt.sign(
    { email: normalizedEmail },
    process.env.JWT_SECRET as string,
    { expiresIn: "7d" },
  );

  res.cookie("session_token", token, {
    httpOnly: true,
    secure: true,
    sameSite: "none",
    maxAge: 7 * 24 * 60 * 60 * 1000,
  });

  res.status(200).json({ email: normalizedEmail });
});

router.post("/logout", (_req: Request, res: Response) => {
  res.clearCookie("session_token");
  res.status(200).json({ message: "Logged out." });
});

router.get("/me", (req: Request, res: Response) => {
  const token = req.cookies?.session_token;
  if (!token) {
    res.status(401).json({ error: "Not authenticated" });
    return;
  }

  try {
    const payload = jwt.verify(token, process.env.JWT_SECRET as string) as {
      email: string;
    };
    res.status(200).json({ email: payload.email });
  } catch {
    res.status(401).json({ error: "Invalid or expired session" });
  }
});

export default router;
