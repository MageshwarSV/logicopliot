"""Reference lists the ERP checks values against (P1, 2026-11-27): ISO 3166 country codes,
ISO 4217 currency codes and the customs Unit Quantity Codes (UQC).

normalize_country() turns a country NAME into its 2-letter code only when the match is
certain (an exact name or a well-known alias) - never a guess. Anything else is returned
unchanged, so the rule check reports it for a person to fix.
"""
from __future__ import annotations

import re

_COUNTRY_TEXT = (
    "AD:Andorra|AE:United Arab Emirates|AF:Afghanistan|AG:Antigua and Barbuda|AI:Anguilla|AL:Albania|"
    "AM:Armenia|AO:Angola|AQ:Antarctica|AR:Argentina|AS:American Samoa|AT:Austria|AU:Australia|AW:Aruba|"
    "AX:Aland Islands|AZ:Azerbaijan|BA:Bosnia and Herzegovina|BB:Barbados|BD:Bangladesh|BE:Belgium|"
    "BF:Burkina Faso|BG:Bulgaria|BH:Bahrain|BI:Burundi|BJ:Benin|BL:Saint Barthelemy|BM:Bermuda|BN:Brunei|"
    "BO:Bolivia|BQ:Bonaire|BR:Brazil|BS:Bahamas|BT:Bhutan|BV:Bouvet Island|BW:Botswana|BY:Belarus|BZ:Belize|"
    "CA:Canada|CC:Cocos Islands|CD:Democratic Republic of the Congo|CF:Central African Republic|CG:Congo|"
    "CH:Switzerland|CI:Ivory Coast|CK:Cook Islands|CL:Chile|CM:Cameroon|CN:China|CO:Colombia|CR:Costa Rica|"
    "CU:Cuba|CV:Cape Verde|CW:Curacao|CX:Christmas Island|CY:Cyprus|CZ:Czech Republic|DE:Germany|DJ:Djibouti|"
    "DK:Denmark|DM:Dominica|DO:Dominican Republic|DZ:Algeria|EC:Ecuador|EE:Estonia|EG:Egypt|EH:Western Sahara|"
    "ER:Eritrea|ES:Spain|ET:Ethiopia|FI:Finland|FJ:Fiji|FK:Falkland Islands|FM:Micronesia|FO:Faroe Islands|"
    "FR:France|GA:Gabon|GB:United Kingdom|GD:Grenada|GE:Georgia|GF:French Guiana|GG:Guernsey|GH:Ghana|"
    "GI:Gibraltar|GL:Greenland|GM:Gambia|GN:Guinea|GP:Guadeloupe|GQ:Equatorial Guinea|GR:Greece|"
    "GS:South Georgia and the South Sandwich Islands|GT:Guatemala|GU:Guam|GW:Guinea-Bissau|GY:Guyana|"
    "HK:Hong Kong|HM:Heard Island and McDonald Islands|HN:Honduras|HR:Croatia|HT:Haiti|HU:Hungary|"
    "ID:Indonesia|IE:Ireland|IL:Israel|IM:Isle of Man|IN:India|IO:British Indian Ocean Territory|IQ:Iraq|"
    "IR:Iran|IS:Iceland|IT:Italy|JE:Jersey|JM:Jamaica|JO:Jordan|JP:Japan|KE:Kenya|KG:Kyrgyzstan|KH:Cambodia|"
    "KI:Kiribati|KM:Comoros|KN:Saint Kitts and Nevis|KP:North Korea|KR:South Korea|KW:Kuwait|"
    "KY:Cayman Islands|KZ:Kazakhstan|LA:Laos|LB:Lebanon|LC:Saint Lucia|LI:Liechtenstein|LK:Sri Lanka|"
    "LR:Liberia|LS:Lesotho|LT:Lithuania|LU:Luxembourg|LV:Latvia|LY:Libya|MA:Morocco|MC:Monaco|MD:Moldova|"
    "ME:Montenegro|MF:Saint Martin|MG:Madagascar|MH:Marshall Islands|MK:North Macedonia|ML:Mali|MM:Myanmar|"
    "MN:Mongolia|MO:Macao|MP:Northern Mariana Islands|MQ:Martinique|MR:Mauritania|MS:Montserrat|MT:Malta|"
    "MU:Mauritius|MV:Maldives|MW:Malawi|MX:Mexico|MY:Malaysia|MZ:Mozambique|NA:Namibia|NC:New Caledonia|"
    "NE:Niger|NF:Norfolk Island|NG:Nigeria|NI:Nicaragua|NL:Netherlands|NO:Norway|NP:Nepal|NR:Nauru|NU:Niue|"
    "NZ:New Zealand|OM:Oman|PA:Panama|PE:Peru|PF:French Polynesia|PG:Papua New Guinea|PH:Philippines|"
    "PK:Pakistan|PL:Poland|PM:Saint Pierre and Miquelon|PN:Pitcairn|PR:Puerto Rico|PS:Palestine|PT:Portugal|"
    "PW:Palau|PY:Paraguay|QA:Qatar|RE:Reunion|RO:Romania|RS:Serbia|RU:Russia|RW:Rwanda|SA:Saudi Arabia|"
    "SB:Solomon Islands|SC:Seychelles|SD:Sudan|SE:Sweden|SG:Singapore|SH:Saint Helena|SI:Slovenia|"
    "SJ:Svalbard and Jan Mayen|SK:Slovakia|SL:Sierra Leone|SM:San Marino|SN:Senegal|SO:Somalia|SR:Suriname|"
    "SS:South Sudan|ST:Sao Tome and Principe|SV:El Salvador|SX:Sint Maarten|SY:Syria|SZ:Eswatini|"
    "TC:Turks and Caicos Islands|TD:Chad|TF:French Southern Territories|TG:Togo|TH:Thailand|TJ:Tajikistan|"
    "TK:Tokelau|TL:Timor-Leste|TM:Turkmenistan|TN:Tunisia|TO:Tonga|TR:Turkey|TT:Trinidad and Tobago|"
    "TV:Tuvalu|TW:Taiwan|TZ:Tanzania|UA:Ukraine|UG:Uganda|UM:United States Minor Outlying Islands|"
    "US:United States|UY:Uruguay|UZ:Uzbekistan|VA:Vatican City|VC:Saint Vincent and the Grenadines|"
    "VE:Venezuela|VG:British Virgin Islands|VI:US Virgin Islands|VN:Vietnam|VU:Vanuatu|WF:Wallis and Futuna|"
    "WS:Samoa|YE:Yemen|YT:Mayotte|ZA:South Africa|ZM:Zambia|ZW:Zimbabwe"
)
COUNTRIES: dict[str, str] = dict(p.split(":", 1) for p in _COUNTRY_TEXT.split("|"))

# Other names a document uses for the same country - all certain.
_ALIASES = {
    "UK": "GB", "U.K.": "GB", "GREAT BRITAIN": "GB", "BRITAIN": "GB", "ENGLAND": "GB", "SCOTLAND": "GB",
    "WALES": "GB", "NORTHERN IRELAND": "GB", "UNITED KINGDOM OF GREAT BRITAIN AND NORTHERN IRELAND": "GB",
    "USA": "US", "U.S.A.": "US", "U.S.": "US", "UNITED STATES OF AMERICA": "US", "AMERICA": "US",
    "UAE": "AE", "U.A.E.": "AE", "DUBAI": "AE", "PRC": "CN", "P.R. CHINA": "CN", "PR CHINA": "CN",
    "PEOPLE'S REPUBLIC OF CHINA": "CN", "PEOPLES REPUBLIC OF CHINA": "CN", "MAINLAND CHINA": "CN",
    "KOREA": "KR", "REPUBLIC OF KOREA": "KR", "KOREA, REPUBLIC OF": "KR", "KOREA (SOUTH)": "KR",
    "KOREA REPUBLIC": "KR", "VIET NAM": "VN", "RUSSIAN FEDERATION": "RU", "TAIWAN, PROVINCE OF CHINA": "TW",
    "TAIWAN (R.O.C.)": "TW", "REPUBLIC OF CHINA": "TW", "HONG KONG SAR": "HK", "HONGKONG": "HK",
    "HONG KONG, CHINA": "HK", "MACAU": "MO", "CZECHIA": "CZ", "HOLLAND": "NL", "THE NETHERLANDS": "NL",
    "TURKIYE": "TR", "TÜRKIYE": "TR", "IRAN, ISLAMIC REPUBLIC OF": "IR", "COTE D'IVOIRE": "CI",
    "CÔTE D'IVOIRE": "CI", "SWAZILAND": "SZ", "BURMA": "MM", "MACEDONIA": "MK", "LAO PDR": "LA",
    "SYRIAN ARAB REPUBLIC": "SY", "BRUNEI DARUSSALAM": "BN", "BOLIVIA, PLURINATIONAL STATE OF": "BO",
    "VENEZUELA, BOLIVARIAN REPUBLIC OF": "VE", "TANZANIA, UNITED REPUBLIC OF": "TZ", "MOLDOVA, REPUBLIC OF": "MD",
    "DEUTSCHLAND": "DE", "FEDERAL REPUBLIC OF GERMANY": "DE", "BHARAT": "IN", "REPUBLIC OF INDIA": "IN",
    "KINGDOM OF SAUDI ARABIA": "SA", "KSA": "SA", "SRI LANKA (CEYLON)": "LK", "PHILIPPINE": "PH",
    "CZECH": "CZ", "SLOVAK REPUBLIC": "SK", "KYRGYZ REPUBLIC": "KG",
}

_NAME_TO_CODE: dict[str, str] = {name.upper(): code for code, name in COUNTRIES.items()}
_NAME_TO_CODE.update(_ALIASES)

CURRENCIES = frozenset((
    "AED AFN ALL AMD ANG AOA ARS AUD AWG AZN BAM BBD BDT BGN BHD BIF BMD BND BOB BRL BSD BTN BWP BYN BZD "
    "CAD CDF CHF CLP CNY COP CRC CUP CVE CZK DJF DKK DOP DZD EGP ERN ETB EUR FJD FKP GBP GEL GHS GIP GMD "
    "GNF GTQ GYD HKD HNL HTG HUF IDR ILS INR IQD IRR ISK JMD JOD JPY KES KGS KHR KMF KPW KRW KWD KYD KZT "
    "LAK LBP LKR LRD LSL LYD MAD MDL MGA MKD MMK MNT MOP MRU MUR MVR MWK MXN MYR MZN NAD NGN NIO NOK NPR "
    "NZD OMR PAB PEN PGK PHP PKR PLN PYG QAR RON RSD RUB RWF SAR SBD SCR SDG SEK SGD SHP SLE SOS SRD SSP "
    "STN SYP SZL THB TJS TMT TND TOP TRY TTD TWD TZS UAH UGX USD UYU UZS VES VND VUV WST XAF XCD XOF XPF "
    "YER ZAR ZMW ZWL"
).split())

# Customs / GST Unit Quantity Codes.
UQC = frozenset((
    "BAG BAL BDL BKL BOU BOX BTL BUN CAN CBM CCM CMS CTN DOZ DRM GGK GMS GRS GYD KGS KLR KME LTR MLT "
    "MTR MTS NOS OTH PAC PCS PRS QTL ROL SET SQF SQM SQY TBS TGM THD TON TUB UGS UNT YDS"
).split())

MASTERS = {
    "country": ("A 2-letter country code, e.g. IN for India, CN for China, PL for Poland.", lambda v: v.upper() in COUNTRIES),
    "currency": ("A 3-letter currency code, e.g. USD, EUR, INR.", lambda v: v.upper() in CURRENCIES),
    "unit": ("A unit code from the customs list, e.g. NOS, KGS, PCS, MTR.", lambda v: v.upper() in UQC),
}


def normalize_country(value) -> str:
    """The 2-letter code for a country written out in full, when certain; else the value as it
    was (a valid code is upper-cased)."""
    raw = "" if value is None else str(value)
    v = raw.strip()
    if not v:
        return raw
    up = re.sub(r"\s+", " ", v).upper().strip(" .")
    if len(up) == 2 and up in COUNTRIES:
        return up
    # "BRAZIL (BR)" - the form an AI-aided box saves: the code in brackets.
    m = re.search(r"\(([A-Z]{2})\)\s*$", up)
    if m and m.group(1) in COUNTRIES:
        return m.group(1)
    hit = _NAME_TO_CODE.get(up) or _NAME_TO_CODE.get(up.replace("&", "AND"))
    return hit or raw


# Currency written as a symbol or a name -> its code, only where there is no doubt ("$" alone
# could be several dollars, "¥" yen or yuan - those are left for a person).
_CURRENCY_ALIASES = {
    "US$": "USD", "US $": "USD", "USD$": "USD", "U.S. DOLLAR": "USD", "U.S. DOLLARS": "USD",
    "US DOLLAR": "USD", "US DOLLARS": "USD", "UNITED STATES DOLLAR": "USD", "AMERICAN DOLLAR": "USD",
    "RS": "INR", "RS.": "INR", "₹": "INR", "RUPEE": "INR", "RUPEES": "INR", "INDIAN RUPEE": "INR",
    "INDIAN RUPEES": "INR", "INR.": "INR",
    "€": "EUR", "EURO": "EUR", "EUROS": "EUR",
    "£": "GBP", "GB POUND": "GBP", "POUND STERLING": "GBP", "POUNDS STERLING": "GBP", "STERLING": "GBP",
    "BRITISH POUND": "GBP", "UK POUND": "GBP",
    "RMB": "CNY", "YUAN": "CNY", "RENMINBI": "CNY", "CHINESE YUAN": "CNY", "CNY.": "CNY",
    "YEN": "JPY", "JAPANESE YEN": "JPY", "S$": "SGD", "SG$": "SGD", "SINGAPORE DOLLAR": "SGD",
    "HK$": "HKD", "HONG KONG DOLLAR": "HKD", "A$": "AUD", "AU$": "AUD", "AUSTRALIAN DOLLAR": "AUD",
    "C$": "CAD", "CA$": "CAD", "CANADIAN DOLLAR": "CAD", "SWISS FRANC": "CHF", "SWISS FRANCS": "CHF",
    "UAE DIRHAM": "AED", "DIRHAM": "AED", "DIRHAMS": "AED", "SAUDI RIYAL": "SAR",
    "MALAYSIAN RINGGIT": "MYR", "RINGGIT": "MYR", "THAI BAHT": "THB", "BAHT": "THB",
    "KOREAN WON": "KRW", "WON": "KRW", "NT$": "TWD", "NEW TAIWAN DOLLAR": "TWD",
}


def normalize_currency(value) -> str:
    """The 3-letter code for a currency written as a symbol or name, when certain; else the
    value as it was (a valid code is upper-cased)."""
    raw = "" if value is None else str(value)
    v = raw.strip()
    if not v:
        return raw
    up = re.sub(r"\s+", " ", v).upper().strip()
    if len(up) == 3 and up in CURRENCIES:
        return up
    m = re.search(r"\(([A-Z]{3})\)\s*$", up)
    if m and m.group(1) in CURRENCIES:
        return m.group(1)
    return _CURRENCY_ALIASES.get(up) or _CURRENCY_ALIASES.get(up.rstrip(".")) or raw


CURRENCY_NAMES = {
    "USD": "US Dollar", "EUR": "Euro", "GBP": "British Pound", "INR": "Indian Rupee", "CNY": "Chinese Yuan",
    "JPY": "Japanese Yen", "SGD": "Singapore Dollar", "HKD": "Hong Kong Dollar", "AUD": "Australian Dollar",
    "CAD": "Canadian Dollar", "CHF": "Swiss Franc", "AED": "UAE Dirham", "SAR": "Saudi Riyal",
    "MYR": "Malaysian Ringgit", "THB": "Thai Baht", "KRW": "Korean Won", "TWD": "New Taiwan Dollar",
    "SEK": "Swedish Krona", "NOK": "Norwegian Krone", "DKK": "Danish Krone", "NZD": "New Zealand Dollar",
    "ZAR": "South African Rand", "IDR": "Indonesian Rupiah", "PHP": "Philippine Peso", "VND": "Vietnamese Dong",
    "BDT": "Bangladeshi Taka", "LKR": "Sri Lankan Rupee", "PKR": "Pakistani Rupee", "NPR": "Nepalese Rupee",
    "RUB": "Russian Ruble", "TRY": "Turkish Lira", "BRL": "Brazilian Real", "MXN": "Mexican Peso",
    "PLN": "Polish Zloty", "CZK": "Czech Koruna", "HUF": "Hungarian Forint", "ILS": "Israeli Shekel",
    "KWD": "Kuwaiti Dinar", "QAR": "Qatari Riyal", "OMR": "Omani Rial", "BHD": "Bahraini Dinar",
}


def master_list(kind: str) -> list[dict]:
    """[{"code", "name"}] for the job screen's search dropdown."""
    if kind == "country":
        return sorted(({"code": c, "name": n} for c, n in COUNTRIES.items()), key=lambda r: r["name"])
    if kind == "currency":
        common = ["INR", "USD", "EUR", "GBP", "CNY", "JPY", "SGD", "AED"]
        rest = sorted(c for c in CURRENCIES if c not in common)
        return [{"code": c, "name": CURRENCY_NAMES.get(c, "")} for c in common + rest]
    return []
