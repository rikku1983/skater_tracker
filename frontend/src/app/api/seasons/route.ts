import { sql } from "@/lib/db"

export const dynamic = "force-dynamic"

export async function GET() {
  const rows = await sql<{ season: string }[]>`
    SELECT DISTINCT season FROM events WHERE track_type='short' ORDER BY season DESC
  `
  return Response.json(rows.map(r => r.season))
}
