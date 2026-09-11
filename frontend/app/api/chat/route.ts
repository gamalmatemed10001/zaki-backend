import { NextRequest, NextResponse } from "next/server";

export async function POST(req: NextRequest) {
  const { text, session_id } = await req.json();

  let backendRes: Response;
  try {
    backendRes = await fetch(`${process.env.ZAKI_BACKEND_URL}/api/assistant`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-API-Key": process.env.ZAKI_API_KEY ?? "",
      },
      body: JSON.stringify({ text, session_id }),
    });
  } catch {
    // Backend unreachable (down, network blip, wrong ZAKI_BACKEND_URL) --
    // never let this Route Handler itself throw, which would surface
    // Next's own generic error response instead of something the chat UI
    // can show as a clean toast.
    return NextResponse.json(
      { detail: "تعذر الاتصال بخادم زكي، تحقق من تشغيله وحاول مرة أخرى." },
      { status: 502 },
    );
  }

  const rawBody = await backendRes.text();
  if (backendRes.ok) {
    return new NextResponse(rawBody, {
      status: backendRes.status,
      headers: { "Content-Type": "application/json" },
    });
  }

  // Backend returned an error status. Its body might be a clean
  // {"detail": "..."} JSON (the common case), or it might be a raw
  // traceback / plain-text "Internal Server Error" if something upstream
  // crashed outside its own error handling — normalize either shape into
  // JSON with a `detail` field so the client never has to guess.
  let detail = "حدث خطأ غير متوقع في الخادم.";
  try {
    const parsed = JSON.parse(rawBody);
    if (typeof parsed?.detail === "string") detail = parsed.detail;
  } catch {
    // rawBody wasn't JSON -- keep the generic Arabic fallback above.
  }
  return NextResponse.json({ detail }, { status: backendRes.status });
}
