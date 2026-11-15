export type Application = {
  id: string; posting_id: string; campaign_id: string; status: string; route: string; skip_reason: string;
  subject: string; body: string; language: string; recipient: string | null; score: number | null;
  check_results: { passed?: boolean; problems?: string[] }; approved_at: string | null; planned_send_at: string | null;
  sent_at: string | null; expires_at: string | null; created_at: string; updated_at: string;
  title: string; company: string; location: string; url: string; cv_label: string; campaign_name: string;
  match_summary?: string; match_strengths?: string[];
};

export type Match = {
  id: string; posting_id: string; decision: string; reason: string; total_score: number | null;
  title: string; company: string; url: string; route: string; created_at: string;
  rubric?: { suggestions?: string[] };
};

export type Campaign = {
  id: string; name: string; state: string; approval_mode: string; auto_above_score: number;
  config: CampaignConfig;
};

export type CampaignConfig = {
  keywords: string[]; countries: string[]; remote_outside_countries: boolean; locations: string[]; work_modes: string[]; contract_types: string[];
  seniority: string[]; languages: string[]; salary_min: number | null; max_posting_age_days: number | null;
  exclude_agencies: boolean; sources: string[]; unknown_policy: Record<string, string>; daily_quota: number;
  send_window: { start: string; end: string; days: number[] }; company_cooldown_days: number;
  contact_cooldown_days: number; match_threshold: number; weights: Record<string, number>; draft_expiry_days: number;
  follow_up_enabled: boolean; follow_up_after_days: number; max_agent_replies: number;
  answer_routine_questions: boolean; thank_you_on_rejection: boolean; may_state_salary: boolean;
  email_min_words: number; email_max_words: number;
};

export type Country = { code: string; name: string; sources: string[]; adzuna_possible: boolean };

export type CV = {
  id: string; label: string; target_roles: string[]; keywords: string[]; language: string; is_default: boolean;
  active: boolean; current_version_id: string | null;
};
