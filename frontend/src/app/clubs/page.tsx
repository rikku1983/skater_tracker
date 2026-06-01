import { sql } from "@/lib/db"
import Link from "next/link"

export const dynamic = "force-dynamic"

export default async function ClubsPage() {
  const clubs = await sql<{ id: number; canonical_name: string; abbreviation: string | null; city: string | null; state: string | null; skater_count: number }[]>`
    SELECT c.id, c.canonical_name, c.abbreviation, c.city, c.state_province as state,
           COUNT(DISTINCT r.skater_id) as skater_count
    FROM clubs c
    LEFT JOIN results r ON r.club_id = c.id
    WHERE NOT (c.country IS NOT NULL AND c.city IS NULL AND c.state_province IS NULL
               AND (length(c.canonical_name) <= 3 OR c.canonical_name = c.country))
    GROUP BY c.id
    ORDER BY skater_count DESC, c.canonical_name
  `

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold">Clubs</h1>

      <div className="border rounded-lg overflow-x-auto">
        <table className="w-full text-sm min-w-[320px]">
          <thead className="bg-muted/50 border-b">
            <tr>
              <th className="text-left px-4 py-2 font-medium">Club</th>
              <th className="text-left px-4 py-2 font-medium hidden sm:table-cell">Abbrev</th>
              <th className="text-left px-4 py-2 font-medium hidden md:table-cell">Location</th>
              <th className="text-right px-4 py-2 font-medium">Skaters</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {clubs.map(c => (
              <tr key={c.id} className="hover:bg-muted/30 transition-colors">
                <td className="px-4 py-2">
                  <Link href={`/clubs/${c.id}`} className="hover:underline font-medium">{c.canonical_name}</Link>
                  {c.city && <div className="text-xs text-muted-foreground md:hidden">{[c.city, c.state].filter(Boolean).join(", ")}</div>}
                </td>
                <td className="px-4 py-2 text-muted-foreground hidden sm:table-cell">{c.abbreviation ?? "—"}</td>
                <td className="px-4 py-2 text-muted-foreground hidden md:table-cell">{[c.city, c.state].filter(Boolean).join(", ") || "—"}</td>
                <td className="px-4 py-2 text-right text-muted-foreground">{Number(c.skater_count).toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
