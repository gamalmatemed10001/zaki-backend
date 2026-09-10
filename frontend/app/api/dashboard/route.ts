import { NextResponse } from "next/server";

export async function GET() {
  const backendRes = await fetch(`${process.env.ZAKI_BACKEND_URL}/api/dashboard`, {
    headers: { "X-API-Key": process.env.ZAKI_API_KEY ?? "" },
    cache: "no-store",
  });

  const body = await backendRes.text();
  return new NextResponse(body, {
    status: backendRes.status,
    headers: { "Content-Type": "application/json" },
  });
}
