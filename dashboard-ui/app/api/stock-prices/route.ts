import { NextResponse } from "next/server";

const API_ORIGIN = process.env.API_ORIGIN || "http://localhost:8000";

export async function GET(request: Request) {
  const source = new URL(request.url);
  const target = new URL("/api/v1/stock-prices", API_ORIGIN);
  target.search = source.search;

  try {
    const response = await fetch(target, { cache: "no-store" });
    const payload = await response.json();
    return NextResponse.json(payload, { status: response.status });
  } catch (cause) {
    const message = cause instanceof Error ? cause.message : "Unknown backend error";
    return NextResponse.json({ error: message }, { status: 502 });
  }
}
