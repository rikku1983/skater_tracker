export interface Overview {
  total_events: number
  total_skaters: number
  total_results: number
  seasons: string[]
}

export interface EventRow {
  id: number
  event_name: string
  season: string
  start_date: string | null
  end_date: string | null
  venue: string | null
  city: string | null
  state: string | null
  track_type: string | null
  result_count: number
}

export interface EventDetail extends EventRow {
  pdf_format: string | null
}

export interface ResultRow {
  id: number
  skater_id: number | null
  skater_name: string
  club_name: string | null
  division: string | null
  distance_m: number | null
  round_type: string | null
  heat: string | null
  rank: number | null
  time_seconds: number | null
  time_text: string | null
  status: string | null
  bib: string | null
  race_number: number | null
  points: number | null
}

export interface ClassificationRow {
  id: number
  skater_id: number | null
  skater_name: string
  club_name: string | null
  division: string | null
  section_type: string | null
  rank: number | null
  points: number | null
  distance_m: number | null
  time_seconds: number | null
  bib: string | null
}

export interface TimeClassificationRow {
  id: number
  skater_id: number | null
  skater_name: string
  club_name: string | null
  division: string | null
  distance_m: number | null
  time_seconds: number | null
  rank: number | null
  bib: string | null
}

export interface SkaterRow {
  id: number
  full_name: string
  first_name: string | null
  last_name: string | null
  gender: string | null
  birth_year: number | null
  primary_club: string | null
  num_results: number
  num_events: number
}

export interface SkaterDetail extends SkaterRow {
  club_id: number | null
  aliases: string[]
}

export interface SeasonBest {
  season: string
  distance_m: number
  best_time: number
  event_name: string
}

export interface EventBest {
  event_id: number
  event_name: string
  start_date: string | null
  season: string
  distance_m: number
  best_time: number
}

export interface ClubRow {
  id: number
  canonical_name: string
  abbreviation: string | null
  city: string | null
  state: string | null
  skater_count: number
}

export interface ClubDetail extends ClubRow {
  alt_names: string | null
}

export interface RosterRow {
  skater_id: number
  full_name: string
  gender: string | null
  birth_year: number | null
  num_results: number
  best_500: number | null
}

export interface LeaderboardRow {
  rank: number
  skater_id: number
  skater_name: string
  club_name: string | null
  best_time: number
  num_races: number
  season: string
}

export interface CompareData {
  skater_id: number
  skater_name: string
  season_bests: SeasonBest[]
  event_bests: EventBest[]
}
