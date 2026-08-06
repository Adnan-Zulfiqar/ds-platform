/** ISO 3166-1 alpha-2 countries shown in the Import as Draft destination picker. */

export type CountryOption = {
  code: string;
  name: string;
};

export const SHIP_TO_COUNTRIES: CountryOption[] = [
  { code: "US", name: "United States" },
  { code: "GB", name: "United Kingdom" },
  { code: "CA", name: "Canada" },
  { code: "AU", name: "Australia" },
  { code: "DE", name: "Germany" },
  { code: "FR", name: "France" },
  { code: "NL", name: "Netherlands" },
  { code: "IT", name: "Italy" },
  { code: "ES", name: "Spain" },
  { code: "PL", name: "Poland" },
  { code: "BE", name: "Belgium" },
  { code: "IE", name: "Ireland" },
  { code: "SE", name: "Sweden" },
  { code: "NZ", name: "New Zealand" },
  { code: "JP", name: "Japan" },
  { code: "SG", name: "Singapore" },
  { code: "MX", name: "Mexico" },
  { code: "AE", name: "United Arab Emirates" },
];

const LAST_SHIP_TO_KEY = "droppilot.lastImportShipTo";

export function countryName(code: string | null | undefined): string {
  if (!code) return "Unknown destination";
  const match = SHIP_TO_COUNTRIES.find((c) => c.code === code.toUpperCase());
  return match?.name ?? code.toUpperCase();
}

export function readLastShipTo(): string | null {
  if (typeof window === "undefined") return null;
  try {
    const value = window.localStorage.getItem(LAST_SHIP_TO_KEY);
    return value && /^[A-Za-z]{2}$/.test(value) ? value.toUpperCase() : null;
  } catch {
    return null;
  }
}

export function persistLastShipTo(code: string): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(LAST_SHIP_TO_KEY, code.toUpperCase());
  } catch {
    // Private mode / quota — non-fatal.
  }
}

export function filterCountries(query: string): CountryOption[] {
  const q = query.trim().toLowerCase();
  if (!q) return SHIP_TO_COUNTRIES;
  return SHIP_TO_COUNTRIES.filter(
    (c) =>
      c.name.toLowerCase().includes(q) || c.code.toLowerCase().includes(q),
  );
}
