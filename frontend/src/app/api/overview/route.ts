import { sql } from "@/lib/db"

export const dynamic = "force-dynamic"

export async function GET() {
  const [eventsR, skatersR, resultsR, seasonsR] = await Promise.all([
    sql`SELECT COUNT(*) as n FROM events WHERE track_type='short'`,
    sql`SELECT COUNT(*) as n FROM skaters`,
    sql`SELECT COUNT(*) as n FROM results r JOIN events e ON e.id=r.event_id WHERE e.track_type='short'`,
    sql<{ season: string }[]>`SELECT DISTINCT season FROM events WHERE track_type='short' ORDER BY season DESC`,
  ])
  return Response.json({
    total_events: Number(eventsR[0].n),
    total_skaters: Number(skatersR[0].n),
    total_results: Number(resultsR[0].n),
    seasons: seasonsR.map(r => r.season),
  })
}
