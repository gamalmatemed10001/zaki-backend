import { NextRequest, NextResponse } from "next/server";

export async function POST(req: NextRequest) {
  const { text, voice } = await req.json();

  const backendRes = await fetch(`${process.env.ZAKI_BACKEND_URL}/api/tts`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-API-Key": process.env.ZAKI_API_KEY ?? "",
    },
    body: JSON.stringify({ text, voice }),
  });

  if (!backendRes.ok) {
    const body = await backendRes.text();
    return new NextResponse(body, {
      status: backendRes.status,
      headers: { "Content-Type": "application/json" },
    });
  }

  const audio = await backendRes.arrayBuffer();
  return new NextResponse(audio, {
    status: 200,
    headers: { "Content-Type": "audio/mpeg" },
  });
}
