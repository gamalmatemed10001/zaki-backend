export class ApiError extends Error {}

export interface ChatResponse {
  reply: string;
  route: string;
  model: string;
}

async function readError(res: Response): Promise<string> {
  try {
    const body = await res.json();
    if (typeof body?.detail === "string") return body.detail;
  } catch {
    // fall through to the generic message below
  }
  // Our own /api/* route handlers always normalize backend errors into
  // {"detail": "..."} JSON (see app/api/chat/route.ts), so this branch
  // should be rare — but if it's ever hit, a raw `${status} ${statusText}`
  // (e.g. "500 Internal Server Error") is not something to show a user.
  return "حدث خطأ غير متوقع أثناء التواصل مع الخادم. حاول مرة أخرى بعد قليل.";
}

export async function sendChat(text: string, sessionId: string): Promise<ChatResponse> {
  const res = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, session_id: sessionId }),
  });

  if (!res.ok) throw new ApiError(await readError(res));
  return res.json();
}

export async function speak(text: string): Promise<Blob> {
  const res = await fetch("/api/speak", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });

  if (!res.ok) throw new ApiError(await readError(res));
  return res.blob();
}
