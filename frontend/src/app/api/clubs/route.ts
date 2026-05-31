import { sql } from "@/lib/db"

export const dynamic = "force-dynamic"

export async function GET() {
  return Response.json(await sql`
    SELECT c.id, c.canonical_name, c.abbreviation, c.city,
           c.state_province as state,
           COUNT(DISTINCT r.skater_id) as skater_count
    FROM clubs c
    LEFT JOIN results r ON r.club_id = c.id
    GROUP BY c.id
    ORDER BY skater_count DESC, c.canonical_name
  `)
}
