import { getDb } from "@/lib/db"
import Link from "next/link"
import { EventsFilter } from "./EventsFilter"

export default async function EventsPage({ searchParams }: { searchParams: Promise<{ season?: string }> }) {
  const { season } = await searchParams
  const db = getDb()

  const seasons = (db.prepare(
    "SELECT DISTINCT season FROM events WHERE track_type='short' ORDER BY season DESC"
  ).all() as { season: string }[]).map(r => r.season)

  let sql = `
    SELECT e.id, e.event_name, e.season, e.event_date as start_date, e.city, e.state,
           COUNT(r.id) as result_count
    FROM events e
    LEFT JOIN results r ON r.event_id = e.id
    WHERE e.track_type = 'short'
  `
  const args: string[] = []
  if (season) { sql += " AND e.season = ?"; args.push(season) }
  sql += ` GROUP BY e.id ORDER BY
    CAST(substr(e.event_date,-4) AS INTEGER) DESC,
    CAST(substr(e.event_date,1,instr(e.event_date,'/')-1) AS INTEGER) DESC,
    CAST(substr(e.event_date,instr(e.event_date,'/')+1) AS INTEGER) DESC,
    e.id DESC`

  const events = db.prepare(sql).all(...args) as { id: number; event_name: string; season: string; start_date: string | null; city: string | null; state: string | null; result_count: number }[]

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Events</h1>
        <EventsFilter seasons={seasons} current={season ?? ""} />
      </div>

      <div className="border rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-muted/50 border-b">
            <tr>
              <th className="text-left px-4 py-2 font-medium">Season</th>
              <th className="text-left px-4 py-2 font-medium">Event</th>
              <th className="text-left px-4 py-2 font-medium">Date</th>
              <th className="text-left px-4 py-2 font-medium">Location</th>
              <th className="text-right px-4 py-2 font-medium">Results</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {events.map(e => (
              <tr key={e.id} className="hover:bg-muted/30 transition-colors">
                <td className="px-4 py-2 text-muted-foreground whitespace-nowrap">{e.season}</td>
                <td className="px-4 py-2">
                  <Link href={`/events/${e.id}`} className="hover:underline font-medium">{e.event_name}</Link>
                </td>
                <td className="px-4 py-2 text-muted-foreground whitespace-nowrap">{e.start_date ?? "—"}</td>
                <td className="px-4 py-2 text-muted-foreground">{[e.city, e.state].filter(Boolean).join(", ") || "—"}</td>
                <td className="px-4 py-2 text-right text-muted-foreground">{e.result_count.toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
