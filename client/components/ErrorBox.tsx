export default function ErrorBox({ error }: { error: string }) {
  // Match error string: 401 {"type":"error","error":{"type":"authentication_error","message":"API key is invalid."},"request_id":null}
  // Extracts the error code and the JSON error object
  const match = error.match(/^(\d{3})\s+(\{[\s\S]*\})$/);

  let code: string | null = null;
  let message: string | null = null;

  if (match) {
    code = match[1];

    try {
      const parsed = JSON.parse(match[2]);
      message = parsed?.error?.message ?? parsed?.message ?? null;
    } catch {
      // JSON couldn't be parsed
    }
  }

  return (
    <div className="h-full w-full flex items-center justify-center">
      <div className="rounded-xl border min-w-[350px] border-solid max-w-88 mx-auto border-neutral-200 bg-white p-4 text-sm text-black">
        <p className="font-semibold text-red-600">Error</p>
        {code && (
          <p className="mt-3">
            <span className="font-semibold">Error code:</span> {code}
          </p>
        )}
        {message && (
          <p className="mt-1">
            <span className="font-semibold">Message:</span> {message}
          </p>
        )}
        {!code && !message && (
          <p className="mt-3 whitespace-pre-wrap">{error}</p>
        )}
      </div>
    </div>
  );
}
