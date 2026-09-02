const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:4000";

export async function requestOtp(email: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/v1/auth/request-otp`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "ngrok-skip-browser-warning": "true",
    },
    body: JSON.stringify({ email }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || "Failed to send code");
  }
}

export async function authenticate(): Promise<boolean> {
  const res = await fetch(`${API_BASE}/api/v1/auth`, {
    credentials: "include",
    headers: { "ngrok-skip-browser-warning": "true" },
  });
  return res.ok;
}

export async function verifyOtp(
  email: string,
  code: string,
): Promise<{ email: string }> {
  const res = await fetch(`${API_BASE}/api/v1/auth/verify-otp`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "ngrok-skip-browser-warning": "true",
    },
    credentials: "include", // required so the browser stores/sends the httpOnly cookie
    body: JSON.stringify({ email, code }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || "Invalid code");
  }
  return res.json();
}

export async function getCurrentUser(): Promise<{ email: string } | null> {
  const res = await fetch(`${API_BASE}/api/v1/auth/me`, {
    credentials: "include",
    headers: { "ngrok-skip-browser-warning": "true" },
  });
  if (!res.ok) return null;
  return res.json();
}

export async function logout(): Promise<void> {
  await fetch(`${API_BASE}/api/v1/auth/logout`, {
    method: "POST",
    credentials: "include",
    headers: { "ngrok-skip-browser-warning": "true" },
  });
}
