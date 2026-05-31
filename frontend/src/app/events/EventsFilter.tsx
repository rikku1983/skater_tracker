"use client"

import { useRouter } from "next/navigation"

export function EventsFilter({ seasons, current }: { seasons: string[]; current: string }) {
  const router = useRouter()
  return (
    <select
      value={current}
      onChange={e => router.push(e.target.value ? `/events?season=${e.target.value}` : "/events")}
      className="border rounded-md px-3 py-2 text-sm bg-background"
    >
      <option value="">All seasons</option>
      {seasons.map(s => <option key={s} value={s}>{s}</option>)}
    </select>
  )
}
