"use client";

import { useState } from "react";
import { useLoad } from "@/components/ui";
import type { Country } from "@/lib/types";

/** The country list from the API, for pickers and for showing names instead of codes. */
export function useCountries() {
  const { data } = useLoad<Country[]>("/api/countries");
  const byCode = new Map((data || []).map((c) => [c.code, c]));
  return { countries: data || [], name: (code: string) => byCode.get(code)?.name || code.toUpperCase(), byCode };
}

/** Choose the countries a search applies within. No country chosen means anywhere. */
const EUROPE = ["at", "be", "bg", "ch", "cy", "cz", "de", "dk", "ee", "es", "fi", "fr", "gb", "gr", "hr", "hu", "ie", "is", "it", "lt",
  "lu", "lv", "mt", "nl", "no", "pl", "pt", "ro", "rs", "se", "si", "sk"];
const GROUPS: { label: string; codes: string[] }[] = [
  { label: "All of Europe", codes: EUROPE }, { label: "Canada", codes: ["ca"] }, { label: "United States", codes: ["us"] },
];

export default function CountryPicker({ value, onChange }: { value: string[]; onChange: (codes: string[]) => void }) {
  const { countries, name, byCode } = useCountries();
  const [query, setQuery] = useState("");
  const q = query.trim().toLowerCase();
  const matches = q
    ? countries.filter((c) => !value.includes(c.code) && (c.name.toLowerCase().includes(q) || c.code === q)).slice(0, 8)
    : [];
  const add = (code: string) => {
    onChange([...value, code]);
    setQuery("");
  };
  const uncovered = value.filter((code) => byCode.has(code) && byCode.get(code)!.sources.length === 0);

  return (
    <div>
      <div className="flex flex-wrap gap-2" aria-label="Chosen countries">
        {value.length === 0 && <span className="text-sm text-slate">Anywhere</span>}
        {value.map((code) => (
          <span key={code} className="inline-flex items-center gap-1 card px-2 py-1 text-sm">
            {name(code)}
            <button type="button" className="text-slate hover:text-problem" aria-label={`Remove ${name(code)}`}
              onClick={() => onChange(value.filter((c) => c !== code))}>×</button>
          </span>
        ))}
      </div>
      <div className="mt-2 flex flex-wrap gap-2">
        {GROUPS.map((g) => (
          <button key={g.label} type="button" className="btn btn-quiet" onClick={() => onChange([...new Set([...value, ...g.codes])])}>
            Add {g.label}
          </button>
        ))}
      </div>
      <input className="field mt-2" placeholder="Add a country: type its name" value={query} aria-label="Add a country"
        onChange={(e) => setQuery(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && matches[0]) {
            e.preventDefault();
            add(matches[0].code);
          }
        }} />
      {matches.length > 0 && (
        <ul className="mt-1 card text-sm" role="listbox">
          {matches.map((c) => (
            <li key={c.code}>
              <button type="button" className="flex w-full justify-between px-3 py-1 text-left hover:bg-rule" onClick={() => add(c.code)}>
                <span>{c.name}</span>
                <span className="text-xs text-slate">{c.sources.length ? c.sources.join(", ") : c.adzuna_possible ? "needs Adzuna key" : "no job source yet"}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {uncovered.length > 0 && (
        <p className="mt-1 text-xs text-waiting">
          No job source searches {uncovered.map(name).join(", ")} yet
          {uncovered.some((c) => byCode.get(c)?.adzuna_possible) ? " (an Adzuna key would add some of them)" : ""}.
          Jobs there are found only through company feeds or links you add.
        </p>
      )}
    </div>
  );
}
