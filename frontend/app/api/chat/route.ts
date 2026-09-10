import { NextRequest, NextResponse } from "next/server";

export async function POST(req: NextRequest) {
  const { text, session_id } = await req.json();

  const backendRes = await fetch(`${process.env.ZAKI_BACKEND_URL}/api/assistant`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-API-Key": process.env.ZAKI_API_KEY ?? "",
    },
    body: JSON.stringify({ text, session_id }),
  });

  const body = await backendRes.text();
  return new NextResponse(body, {
    status: backendRes.status,
    headers: { "Content-Type": "application/json" },
  });
}
