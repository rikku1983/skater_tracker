import type {
  Overview, EventRow, EventDetail, ResultRow, ClassificationRow,
  TimeClassificationRow, SkaterRow, SkaterDetail, SeasonBest, EventBest,
  ClubRow, ClubDetail, RosterRow, LeaderboardRow, CompareData,
} from "./types"

const BASE = typeof window === "undefined" ? "http://localhost:3000" : ""

async function get<T>(path: string, params?: Record<string, string | number | undefined>): Promise<T> {
  const url = new URL(`${BASE}/api${path}`)
  if (params) {
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== "") url.searchParams.set(k, String(v))
    })
  }
  const res = await fetch(url.toString(), { cache: "no-store" })
  if (!res.ok) throw new Error(`API error ${res.status}: ${path}`)
  return res.json() as Promise<T>
}

export const api = {
  overview: () => get<Overview>("/overview"),
  seasons: () => get<string[]>("/seasons"),

  events: (params?: { season?: string; track_type?: string }) => get<EventRow[]>("/events", params),
  event: (id: number) => get<EventDetail>(`/events/${id}`),
  eventResults: (id: number, params?: { division?: string; distance_m?: number; round_type?: string }) =>
    get<ResultRow[]>(`/events/${id}/results`, params),
  eventClassification: (id: number, params?: { section_type?: string; division?: string }) =>
    get<ClassificationRow[]>(`/events/${id}/classification`, params),
  eventTimeClassification: (id: number) =>
    get<TimeClassificationRow[]>(`/events/${id}/time_classification`),

  skaters: (params?: { q?: string; club_id?: number; gender?: string; limit?: number }) =>
    get<SkaterRow[]>("/skaters", params),
  skater: (id: number) => get<SkaterDetail>(`/skaters/${id}`),
  skaterResults: (id: number) => get<ResultRow[]>(`/skaters/${id}/results`),
  skaterSeasonBests: (id: number) => get<SeasonBest[]>(`/skaters/${id}/season_bests`),
  skaterEventBests: (id: number) => get<EventBest[]>(`/skaters/${id}/event_bests`),

  clubs: () => get<ClubRow[]>("/clubs"),
  club: (id: number) => get<ClubDetail>(`/clubs/${id}`),
  clubRoster: (id: number, params?: { season?: string }) =>
    get<RosterRow[]>(`/clubs/${id}/roster`, params),

  leaderboard: (params: { season: string; distance_m: number; gender?: string; division?: string }) =>
    get<LeaderboardRow[]>("/leaderboard", params),

  compare: (ids: number[]) => get<CompareData[]>(`/compare?ids=${ids.join(",")}`),
}
