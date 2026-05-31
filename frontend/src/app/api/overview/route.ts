import { getDb } from "@/lib/db"

export function GET() {
  const db = getDb()

  const total_events = (db.prepare("SELECT COUNT(*) as n FROM events WHERE track_type='short'").get() as { n: number }).n
  const total_skaters = (db.prepare("SELECT COUNT(*) as n FROM skaters").get() as { n: number }).n
  const total_results = (db.prepare("SELECT COUNT(*) as n FROM results r JOIN events e ON e.id=r.event_id WHERE e.track_type='short'").get() as { n: number }).n
  const seasons = (db.prepare("SELECT DISTINCT season FROM events WHERE track_type='short' ORDER BY season DESC").all() as { season: string }[]).map(r => r.season)

  return Response.json({ total_events, total_skaters, total_results, seasons })
}
