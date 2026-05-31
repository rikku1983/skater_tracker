import { sql } from "@/lib/db"
import { LeaderboardClient } from "./LeaderboardClient"

export const dynamic = "force-dynamic"

export default async function LeaderboardPage() {
  const [seasonsR, distancesR, birthYearsR] = await Promise.all([
    sql<{ season: string }[]>`SELECT DISTINCT season FROM events WHERE track_type='short' ORDER BY season DESC`,
    sql<{ distance_m: number }[]>`
      SELECT DISTINCT distance_m FROM results r
      JOIN events e ON e.id=r.event_id
      WHERE e.track_type='short' AND r.distance_m IS NOT NULL
      ORDER BY distance_m
    `,
    sql<{ birth_year: number }[]>`
      SELECT DISTINCT s.birth_year
      FROM skaters s
      JOIN results r ON r.skater_id = s.id
      JOIN events e ON e.id = r.event_id
      WHERE s.birth_year IS NOT NULL AND e.track_type = 'short'
      ORDER BY s.birth_year DESC
    `,
  ])

  return (
    <div className="space-y-6">
      <div
        className="relative rounded-2xl overflow-hidden"
        style={{ backgroundImage: "url('/leaderboard.png')", backgroundSize: "cover", backgroundPosition: "center" }}
      >
        <div className="absolute inset-0 bg-black/25" />
        <div className="relative px-8 py-12" style={{ textShadow: "0 1px 6px rgba(0,0,0,0.7)" }}>
          <h1 className="text-4xl font-bold text-white mb-2">Leaderboard</h1>
          <p className="text-white/80 text-lg">Top times by season and distance</p>
        </div>
      </div>
      <LeaderboardClient
        seasons={seasonsR.map(r => r.season)}
        distances={distancesR.map(r => r.distance_m)}
        birthYears={birthYearsR.map(r => r.birth_year)}
      />
    </div>
  )
}
