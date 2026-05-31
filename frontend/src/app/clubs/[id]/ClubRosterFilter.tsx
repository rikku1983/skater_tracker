"use client"

import { useRouter } from "next/navigation"

export function ClubRosterFilter({ seasons, current, clubId }: { seasons: string[]; current: string; clubId: number }) {
  const router = useRouter()
  return (
    <select
      value={current}
      onChange={e => router.push(e.target.value ? `/clubs/${clubId}?season=${e.target.value}` : `/clubs/${clubId}`)}
      className="border rounded-md px-3 py-2 text-sm bg-background"
    >
      <option value="">All seasons</option>
      {seasons.map(s => <option key={s} value={s}>{s}</option>)}
    </select>
  )
}
