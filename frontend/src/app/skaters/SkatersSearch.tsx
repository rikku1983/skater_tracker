"use client"

import { useState, useEffect } from "react"
import { useSearchParams } from "next/navigation"
import Link from "next/link"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import type { SkaterRow } from "@/lib/types"

export function SkatersSearch() {
  const searchParams = useSearchParams()
  const [q, setQ] = useState(searchParams.get("q") ?? "")
  const [gender, setGender] = useState(searchParams.get("gender") ?? "")
  const [skaters, setSkaters] = useState<SkaterRow[]>([])
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!q.trim() && !gender) {
      setSkaters([])
      return
    }
    const params = new URLSearchParams()
    if (q) params.set("q", q)
    if (gender) params.set("gender", gender)
    params.set("limit", "100")
    setLoading(true)
    const ctrl = new AbortController()
    fetch(`/api/skaters?${params}`, { signal: ctrl.signal })
      .then(r => r.json())
      .then(d => { setSkaters(d); setLoading(false) })
      .catch(() => {})
    return () => ctrl.abort()
  }, [q, gender])

  return (
    <>
      <div className="flex gap-3">
        <Input
          placeholder="Search by name…"
          value={q}
          onChange={e => setQ(e.target.value)}
          className="max-w-xs"
          autoFocus
        />
        <select
          value={gender}
          onChange={e => setGender(e.target.value)}
          className="border rounded-md px-3 py-2 text-sm bg-background"
        >
          <option value="">All genders</option>
          <option value="Male">Male</option>
          <option value="Female">Female</option>
        </select>
      </div>

      {!q.trim() && !gender && (
        <p className="text-sm text-muted-foreground">Enter a name or select a gender filter to search.</p>
      )}

      {loading && <div className="text-sm text-muted-foreground">Searching…</div>}

      {(q.trim() || gender) && !loading && (
        <div className="border rounded-lg overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-muted/50 border-b">
              <tr>
                <th className="text-left px-4 py-2 font-medium">Name</th>
                <th className="text-left px-4 py-2 font-medium">Gender</th>
                <th className="text-left px-4 py-2 font-medium">Born</th>
                <th className="text-left px-4 py-2 font-medium">Club</th>
                <th className="text-right px-4 py-2 font-medium">Results</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {skaters.map(s => (
                <tr key={s.id} className="hover:bg-muted/30 transition-colors">
                  <td className="px-4 py-2">
                    <Link href={`/skaters/${s.id}`} className="hover:underline font-medium">{s.full_name}</Link>
                  </td>
                  <td className="px-4 py-2">
                    {s.gender ? (
                      <Badge variant={s.gender === "Male" ? "secondary" : "outline"} className="text-xs">{s.gender}</Badge>
                    ) : "—"}
                  </td>
                  <td className="px-4 py-2 text-muted-foreground">{s.birth_year ?? "—"}</td>
                  <td className="px-4 py-2 text-muted-foreground">{s.primary_club ?? "—"}</td>
                  <td className="px-4 py-2 text-right text-muted-foreground">{s.num_results.toLocaleString()}</td>
                </tr>
              ))}
              {skaters.length === 0 && (
                <tr><td colSpan={5} className="px-4 py-8 text-center text-muted-foreground">No skaters found</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}
