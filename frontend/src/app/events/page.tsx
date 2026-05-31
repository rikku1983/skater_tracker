import { sql } from "@/lib/db"
import Link from "next/link"
import { EventsFilter } from "./EventsFilter"

export const dynamic = "force-dynamic"

export default async function EventsPage({ searchParams }: { searchParams: Promise<{ season?: string }> }) {
  const { season } = await searchParams

  const seasons = (await sql<{ season: string }[]>`
    SELECT DISTINCT season FROM events WHERE track_type='short' ORDER BY season DESC
  `).map(r => r.season)

  const args: (string | number | boolean | null)[] = []
  let q = `
    SELECT e.id, e.event_name, e.season, e.event_date as start_date, e.city, e.state,
           COUNT(r.id) as result_count
    FROM events e
    LEFT JOIN results r ON r.event_id = e.id
    WHERE e.track_type = 'short'
  `
  if (season) { args.push(season); q += ` AND e.season = $${args.length}` }
  q += ` GROUP BY e.id ORDER BY
    SPLIT_PART(e.event_date,'/',3)::INTEGER DESC,
    SPLIT_PART(e.event_date,'/',1)::INTEGER DESC,
    SPLIT_PART(e.event_date,'/',2)::INTEGER DESC,
    e.id DESC`

  const events = await sql.unsafe<{ id: number; event_name: string; season: string; start_date: string | null; city: string | null; state: string | null; result_count: number }[]>(q, args)

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Events</h1>
        <EventsFilter seasons={seasons} current={season ?? ""} />
      </div>

      <div className="border rounded-lg overflow-x-auto">
        <table className="w-full text-sm min-w-[480px]">
          <thead className="bg-muted/50 border-b">
            <tr>
              <th className="text-left px-4 py-2 font-medium hidden sm:table-cell">Season</th>
              <th className="text-left px-4 py-2 font-medium">Event</th>
              <th className="text-left px-4 py-2 font-medium hidden sm:table-cell">Date</th>
              <th className="text-left px-4 py-2 font-medium hidden md:table-cell">Location</th>
              <th className="text-right px-4 py-2 font-medium">Results</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {events.map(e => (
              <tr key={e.id} className="hover:bg-muted/30 transition-colors">
                <td className="px-4 py-2 text-muted-foreground whitespace-nowrap hidden sm:table-cell">{e.season}</td>
                <td className="px-4 py-2">
                  <Link href={`/events/${e.id}`} className="hover:underline font-medium">{e.event_name}</Link>
                  <div className="text-xs text-muted-foreground sm:hidden">{e.season}{e.start_date ? ` · ${e.start_date}` : ""}</div>
                </td>
                <td className="px-4 py-2 text-muted-foreground whitespace-nowrap hidden sm:table-cell">{e.start_date ?? "—"}</td>
                <td className="px-4 py-2 text-muted-foreground hidden md:table-cell">{[e.city, e.state].filter(Boolean).join(", ") || "—"}</td>
                <td className="px-4 py-2 text-right text-muted-foreground">{Number(e.result_count).toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
