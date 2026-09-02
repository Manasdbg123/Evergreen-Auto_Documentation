import type { Request, Response, NextFunction } from "express";
import jwt from "jsonwebtoken";

export interface AuthedRequest extends Request {
  userEmail?: string;
}

export function requireAuth(
  req: AuthedRequest,
  res: Response,
  next: NextFunction,
): void {
  // Only authenticate for methods that modify data
  const writeMethods = ["POST", "PUT", "PATCH", "DELETE"];
  if (!writeMethods.includes(req.method)) {
    return next();
  }

  const token = req.cookies?.session_token;
  if (!token) {
    res.status(401).json({ error: "Authentication required" });
    return;
  }

  try {
    const payload = jwt.verify(token, process.env.JWT_SECRET as string) as {
      email: string;
    };
    req.userEmail = payload.email;
    next();
  } catch {
    res.status(401).json({ error: "Invalid or expired session" });
  }
}
