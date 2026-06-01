import { sql } from "@/lib/db"
import { notFound } from "next/navigation"
import Link from "next/link"
import { Badge } from "@/components/ui/badge"
import { formatTime } from "@/lib/utils"
import { ClubRosterFilter } from "./ClubRosterFilter"

export const dynamic = "force-dynamic"

export default async function ClubDetailPage({ params, searchParams }: {
  params: Promise<{ id: string }>
  searchParams: Promise<{ season?: string }>
}) {
  const { id } = await params
  const { season } = await searchParams
  const cid = Number(id)

  const clubRows = await sql`
    SELECT c.id, c.canonical_name, c.abbreviation, c.city, c.state_province as state,
           COUNT(DISTINCT r.skater_id) as skater_count
    FROM clubs c
    LEFT JOIN results r ON r.club_id = c.id
    WHERE c.id = ${cid}
    GROUP BY c.id
  `
  if (!clubRows[0]) notFound()
  const club = clubRows[0] as { id: number; canonical_name: string; abbreviation: string | null; city: string | null; state: string | null; skater_count: number }

  const seasons = (await sql<{ season: string }[]>`
    SELECT DISTINCT season FROM events WHERE track_type='short' ORDER BY season DESC
  `).map(r => r.season)

  const args: (string | number | boolean | null)[] = [cid]
  let q = `
    SELECT s.id as skater_id, s.full_name, s.gender, s.birth_year,
           COUNT(r.id) as num_results,
           MIN(CASE WHEN r.distance_m=500 AND r.time_seconds IS NOT NULL
               AND COALESCE(r.status,'') NOT IN ('DNS','DNS+','DNF','DNF+','DQ','DQ+','FNT','no contest')
               THEN r.time_seconds END) as best_500
    FROM skaters s
    JOIN results r ON r.skater_id = s.id
    JOIN events e ON e.id = r.event_id
    WHERE r.club_id = $1 AND e.track_type = 'short' AND r.is_relay = false
  `
  if (season) { args.push(season); q += ` AND e.season = $${args.length}` }
  q += " GROUP BY s.id ORDER BY num_results DESC"

  const roster = await sql.unsafe<{ skater_id: number; full_name: string; gender: string | null; birth_year: number | null; num_results: number; best_500: number | null }[]>(q, args)

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold mb-2">{club.canonical_name}</h1>
        <div className="flex gap-2 flex-wrap">
          {club.abbreviation && <Badge variant="secondary">{club.abbreviation}</Badge>}
          {(club.city || club.state) && <Badge variant="outline">{[club.city, club.state].filter(Boolean).join(", ")}</Badge>}
          <Badge variant="outline">{Number(club.skater_count).toLocaleString()} skaters</Badge>
        </div>
      </div>

      <div className="flex items-center justify-between">
        <h2 className="text-xl font-semibold">Roster</h2>
        <ClubRosterFilter seasons={seasons} current={season ?? ""} clubId={cid} />
      </div>

      <div className="border rounded-lg overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-muted/50 border-b">
            <tr>
              <th className="text-left px-4 py-2 font-medium">Name</th>
              <th className="text-left px-4 py-2 font-medium hidden sm:table-cell">Gender</th>
              <th className="text-left px-4 py-2 font-medium hidden sm:table-cell">Born</th>
              <th className="text-right px-4 py-2 font-medium">Results</th>
              <th className="text-right px-4 py-2 font-medium">Best 500m</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {roster.map(s => (
              <tr key={s.skater_id} className="hover:bg-muted/30 transition-colors">
                <td className="px-4 py-2">
                  <Link href={`/skaters/${s.skater_id}`} className="hover:underline font-medium">{s.full_name}</Link>
                </td>
                <td className="px-4 py-2 text-muted-foreground hidden sm:table-cell">{s.gender ?? "—"}</td>
                <td className="px-4 py-2 text-muted-foreground hidden sm:table-cell">{s.birth_year ?? "—"}</td>
                <td className="px-4 py-2 text-right text-muted-foreground">{Number(s.num_results).toLocaleString()}</td>
                <td className="px-4 py-2 text-right font-mono">{s.best_500 != null ? formatTime(Number(s.best_500)) : "—"}</td>
              </tr>
            ))}
            {roster.length === 0 && (
              <tr><td colSpan={5} className="px-4 py-8 text-center text-muted-foreground">No skaters</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
