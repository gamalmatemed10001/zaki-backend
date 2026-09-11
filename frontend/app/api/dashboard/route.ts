import { NextResponse } from "next/server";

export async function GET() {
  let backendRes: Response;
  try {
    backendRes = await fetch(`${process.env.ZAKI_BACKEND_URL}/api/dashboard`, {
      headers: { "X-API-Key": process.env.ZAKI_API_KEY ?? "" },
      cache: "no-store",
    });
  } catch {
    // Backend unreachable -- never let this Route Handler itself throw.
    // lib/dashboard.ts's fetchDashboard() treats any non-ok response as
    // "keep whatever widget data we already have, surface a soft error"
    // rather than crashing the widgets.
    return NextResponse.json(
      { detail: "تعذر الاتصال بخادم زكي لتحميل لوحة المعلومات." },
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

  let detail = "تعذر تحميل لوحة المعلومات.";
  try {
    const parsed = JSON.parse(rawBody);
    if (typeof parsed?.detail === "string") detail = parsed.detail;
  } catch {
    // rawBody wasn't JSON -- keep the generic Arabic fallback above.
  }
  return NextResponse.json({ detail }, { status: backendRes.status });
}
