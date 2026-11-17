/** Country names as stored in the importer master data (upper case, ERP spelling, sometimes
 *  cut at 30 characters) -> ISO 3166 alpha-2, for the flag shown beside the name. */
const ISO2: Record<string, string> = {
  AFGHANISTAN: "AF", ALBANIA: "AL", ALGERIA: "DZ", "AMERICAN SAMOA": "AS", ANGOLA: "AO", ANGUILLA: "AI",
  "ANTIGUA AND BARBUDA": "AG", ARGENTINA: "AR", ARMENIA: "AM", AUSTRALIA: "AU", AUSTRIA: "AT", AZERBAIJAN: "AZ",
  BAHAMAS: "BS", BAHRAIN: "BH", BANGLADESH: "BD", BARBADOS: "BB", BELARUS: "BY", BELGIUM: "BE", BELIZE: "BZ",
  BENIN: "BJ", BERMUDA: "BM", "BOSNIA AND HERZEGOVINA": "BA", BRAZIL: "BR", "BRUNEI DARUSSALAM": "BN",
  BULGARIA: "BG", "BURKINA FASO": "BF", CAMBODIA: "KH", CAMEROON: "CM", CANADA: "CA", "CAYMAN ISLANDS": "KY",
  CHILE: "CL", CHINA: "CN", COLOMBIA: "CO", CONGO: "CG", "COSTA RICA": "CR", "COTE D'IVOIRE": "CI", CROATIA: "HR",
  CURACAO: "CW", CYPRUS: "CY", "CZECH REPUBLIC": "CZ", DENMARK: "DK", DJIBOUTI: "DJ", "DOMINICAN REPUBLIC": "DO",
  ECUADOR: "EC", EGYPT: "EG", "EL SALVADOR": "SV", ESTONIA: "EE", ETHIOPIA: "ET", FINLAND: "FI", FRANCE: "FR",
  "FRENCH POLYNESIA": "PF", GABON: "GA", GAMBIA: "GM", GAUTEMALA: "GT", GUATEMALA: "GT", GERMANY: "DE", GHANA: "GH",
  GREECE: "GR", GREENLAND: "GL", GRENADA: "GD", GUADELOUPE: "GP", GUINEA: "GN", GUYANA: "GY", HAITI: "HT",
  HONDURAS: "HN", "HONG KONG": "HK", HUNGARY: "HU", ICELAND: "IS", INDIA: "IN", INDONESIA: "ID", IRAN: "IR",
  IRAQ: "IQ", IRELAND: "IE", ISRAEL: "IL", ITALY: "IT", JAMAICA: "JM", JAPAN: "JP", JORDAN: "JO", KAZAKHSTAN: "KZ",
  KENYA: "KE", "KOREA, REPUBLIC OF": "KR", KUWAIT: "KW", "LAO PEOPLE'S DEMOCRATIC REPUBL": "LA", LATVIA: "LV",
  LEBANON: "LB", LESOTHO: "LS", LIBERIA: "LR", "LIBYAN ARAB REPUBLIC": "LY", LITHUANIA: "LT", LUXEMBOURG: "LU",
  MACAO: "MO", "MACEDONIA, THE FORMER YUGOSLAV": "MK", MADAGASCAR: "MG", MALAWI: "MW", MALAYSIA: "MY",
  MALDIVES: "MV", MALI: "ML", MALTA: "MT", "MARSHALL ISLANDS": "MH", MAURITANIA: "MR", MAURITIUS: "MU",
  MEXICO: "MX", MONACO: "MC", MOROCCO: "MA", MOZAMBIQUE: "MZ", MYANMAR: "MM", NAMIBIA: "NA", NEPAL: "NP",
  NETHERLANDS: "NL", "NETHERLANDS ANTILLES": "CW", "NEW CALEDONIA": "NC", "NEW ZEALAND": "NZ", NIGERIA: "NG",
  NORWAY: "NO", OMAN: "OM", PAKISTAN: "PK", "PALESTINE STATE": "PS", PANAMA: "PA", "PAPUA NEW GUINEA": "PG",
  PERU: "PE", PHILIPPINES: "PH", POLAND: "PL", PORTUGAL: "PT", QATAR: "QA", REUNION: "RE", ROMANIA: "RO",
  RUSSIA: "RU", RWANDA: "RW", "SAINT KITTS AND NEVIS": "KN", "SAINT VINCENT AND THE GRENADIN": "VC", SAMOA: "WS",
  "SAN MARINO": "SM", "SAUDI ARABIA": "SA", SENEGAL: "SN", SERBIA: "RS", SEYCHELLES: "SC", "SIERRA LEONE": "SL",
  SINGAPORE: "SG", "SLOVAK REPUBLIC": "SK", SLOVENIA: "SI", SOMALIA: "SO", "SOUTH AFRICA": "ZA", SPAIN: "ES",
  "SRI LANKA": "LK", SUDAN: "SD", SURINAME: "SR", SWAZILAND: "SZ", SWEDEN: "SE", SWITZERLAND: "CH",
  "SYRIAN ARAB REPUBLIC": "SY", TAIWAN: "TW", TANZANIA: "TZ", THAILAND: "TH", TOGO: "TG",
  "TRINIDAD AND TOBAGO": "TT", TUNISIA: "TN", TURKEY: "TR", UGANDA: "UG", UKRAINE: "UA",
  "UNITED ARAB EMIRATES": "AE", "UNITED KINGDOM": "GB", "UNITED STATES": "US", URUGUAY: "UY", UZBEKISTAN: "UZ",
  VENEZUELA: "VE", "VIETNAM, DEMOCRATIC REP. OF": "VN", VIETNAM: "VN", "VIRGIN ISLANDS, BRITISH": "VG",
  YEMEN: "YE", ZAMBIA: "ZM", ZIMBABWE: "ZW",
};

/** Flag emoji for a stored country name, or "" when unknown. (Windows shows flag emoji as
 *  the two letters, e.g. "IN" - still readable, just not a picture.) */
export function countryFlag(name: string): string {
  const code = ISO2[(name || "").trim().toUpperCase()];
  if (!code) return "";
  return String.fromCodePoint(...[...code].map((c) => 0x1f1e6 + c.charCodeAt(0) - 65));
}

const SMALL_WORDS = new Set(["and", "of", "the"]);

/** "UNITED STATES" -> "United States", "KOREA, REPUBLIC OF" -> "Korea, Republic of". */
export function countryLabel(name: string): string {
  return (name || "")
    .toLowerCase()
    .split(" ")
    .map((w, i) => (i > 0 && SMALL_WORDS.has(w) ? w : w.charAt(0).toUpperCase() + w.slice(1)))
    .join(" ");
}
