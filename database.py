import sqlite3
from typing import List, Dict, Optional
from datetime import datetime

import os

# ---------------------------------------------------------------------------
# All FIDE member federations — idempotent seed used at startup
# Format: (name, abbreviation, country_code, country_name, rating_system,
#          website_url, tournaments_url)
# ---------------------------------------------------------------------------
_ALL_FEDERATIONS = [
    # ── AFRICA ──────────────────────────────────────────────────────────────
    ("Algerian Chess Federation",                "ALG", "DZ", "Algeria",                  "FIDE Rating", "https://www.algerchess.org",            ""),
    ("Angola Chess Federation",                  "ANG", "AO", "Angola",                   "FIDE Rating", "",                                      ""),
    ("Benin Chess Federation",                   "BEN", "BJ", "Benin",                    "FIDE Rating", "",                                      ""),
    ("Botswana Chess Association",               "BOT", "BW", "Botswana",                 "FIDE Rating", "",                                      ""),
    ("Burkina Faso Chess Federation",            "BUF", "BF", "Burkina Faso",             "FIDE Rating", "",                                      ""),
    ("Burundian Chess Federation",               "BDI", "BI", "Burundi",                  "FIDE Rating", "",                                      ""),
    ("Cameroonian Chess Federation",             "CMR", "CM", "Cameroon",                 "FIDE Rating", "",                                      ""),
    ("Cape Verde Chess Federation",              "CPV", "CV", "Cape Verde",               "FIDE Rating", "",                                      ""),
    ("Central African Chess Federation",         "CAF", "CF", "Central African Republic", "FIDE Rating", "",                                      ""),
    ("Chad Chess Federation",                    "CHA", "TD", "Chad",                     "FIDE Rating", "",                                      ""),
    ("Comoros Chess Federation",                 "COM", "KM", "Comoros",                  "FIDE Rating", "",                                      ""),
    ("Congo Chess Federation",                   "CGO", "CG", "Congo",                    "FIDE Rating", "",                                      ""),
    ("DR Congo Chess Federation",                "COD", "CD", "DR Congo",                 "FIDE Rating", "",                                      ""),
    ("Ivory Coast Chess Federation",             "CIV", "CI", "Côte d'Ivoire",            "FIDE Rating", "",                                      ""),
    ("Djibouti Chess Federation",                "DJI", "DJ", "Djibouti",                 "FIDE Rating", "",                                      ""),
    ("Egyptian Chess Federation",                "EGY", "EG", "Egypt",                    "FIDE Rating", "https://www.egyptchess.com",             ""),
    ("Equatorial Guinea Chess Federation",       "GEQ", "GQ", "Equatorial Guinea",        "FIDE Rating", "",                                      ""),
    ("Eritrea Chess Federation",                 "ERI", "ER", "Eritrea",                  "FIDE Rating", "",                                      ""),
    ("Ethiopian Chess Federation",               "ETH", "ET", "Ethiopia",                 "FIDE Rating", "",                                      ""),
    ("Gabonese Chess Federation",                "GAB", "GA", "Gabon",                    "FIDE Rating", "",                                      ""),
    ("Gambia Chess Association",                 "GAM", "GM", "Gambia",                   "FIDE Rating", "",                                      ""),
    ("Ghana Chess Federation",                   "GHA", "GH", "Ghana",                    "FIDE Rating", "",                                      ""),
    ("Guinea Chess Federation",                  "GUI", "GN", "Guinea",                   "FIDE Rating", "",                                      ""),
    ("Guinea-Bissau Chess Federation",           "GBS", "GW", "Guinea-Bissau",            "FIDE Rating", "",                                      ""),
    ("Kenya Chess Federation",                   "KEN", "KE", "Kenya",                    "FIDE Rating", "https://www.kenyachess.co.ke",           ""),
    ("Lesotho Chess Association",                "LES", "LS", "Lesotho",                  "FIDE Rating", "",                                      ""),
    ("Liberian Chess Federation",                "LBR", "LR", "Liberia",                  "FIDE Rating", "",                                      ""),
    ("Libyan Chess Federation",                  "LBA", "LY", "Libya",                    "FIDE Rating", "",                                      ""),
    ("Madagascar Chess Federation",              "MAD", "MG", "Madagascar",               "FIDE Rating", "",                                      ""),
    ("Malawi Chess Federation",                  "MAW", "MW", "Malawi",                   "FIDE Rating", "",                                      ""),
    ("Mali Chess Federation",                    "MLI", "ML", "Mali",                     "FIDE Rating", "",                                      ""),
    ("Mauritanian Chess Federation",             "MTN", "MR", "Mauritania",               "FIDE Rating", "",                                      ""),
    ("Mauritius Chess Federation",               "MRI", "MU", "Mauritius",                "FIDE Rating", "",                                      ""),
    ("Royal Moroccan Chess Federation",          "MAR", "MA", "Morocco",                  "FIDE Rating", "https://www.royalechecs.ma",             ""),
    ("Mozambique Chess Federation",              "MOZ", "MZ", "Mozambique",               "FIDE Rating", "",                                      ""),
    ("Chess Federation of Namibia",              "NAM", "NA", "Namibia",                  "FIDE Rating", "https://www.namibiachess.org",           "https://www.namibiachess.org/tournaments"),
    ("Niger Chess Federation",                   "NIG", "NE", "Niger",                    "FIDE Rating", "",                                      ""),
    ("Nigerian Chess Federation",                "NGR", "NG", "Nigeria",                  "FIDE Rating", "https://www.nigeriachess.com",           ""),
    ("Rwanda Chess Federation",                  "RWA", "RW", "Rwanda",                   "FIDE Rating", "",                                      ""),
    ("São Tomé and Príncipe Chess Federation",   "STP", "ST", "São Tomé and Príncipe",    "FIDE Rating", "",                                      ""),
    ("Senegalese Chess Federation",              "SEN", "SN", "Senegal",                  "FIDE Rating", "",                                      ""),
    ("Seychelles Chess Federation",              "SEY", "SC", "Seychelles",               "FIDE Rating", "",                                      ""),
    ("Sierra Leone Chess Federation",            "SLE", "SL", "Sierra Leone",             "FIDE Rating", "",                                      ""),
    ("Somali Chess Federation",                  "SOM", "SO", "Somalia",                  "FIDE Rating", "",                                      ""),
    ("South African Chess Federation",           "RSA", "ZA", "South Africa",             "FIDE Rating", "https://www.chesssa.co.za",              "https://www.chesssa.co.za/tournaments"),
    ("South Sudan Chess Federation",             "SSD", "SS", "South Sudan",              "FIDE Rating", "",                                      ""),
    ("Sudan Chess Federation",                   "SUD", "SD", "Sudan",                    "FIDE Rating", "",                                      ""),
    ("Eswatini Chess Federation",                "SWZ", "SZ", "Eswatini",                 "FIDE Rating", "",                                      ""),
    ("Tanzania Chess Association",               "TAN", "TZ", "Tanzania",                 "FIDE Rating", "",                                      ""),
    ("Togo Chess Federation",                    "TOG", "TG", "Togo",                     "FIDE Rating", "",                                      ""),
    ("Tunisian Chess Federation",                "TUN", "TN", "Tunisia",                  "FIDE Rating", "https://www.fide.tn",                    ""),
    ("Uganda Chess Federation",                  "UGA", "UG", "Uganda",                   "FIDE Rating", "",                                      ""),
    ("Zambia Chess Federation",                  "ZAM", "ZM", "Zambia",                   "FIDE Rating", "",                                      ""),
    ("Zimbabwe Chess Federation",                "ZIM", "ZW", "Zimbabwe",                 "FIDE Rating", "",                                      ""),

    # ── AMERICAS ────────────────────────────────────────────────────────────
    ("Argentine Chess Federation",               "ARG", "AR", "Argentina",                "FIDE Rating", "https://www.ajedrezargentino.com.ar",    ""),
    ("Bahamas Chess Federation",                 "BAH", "BS", "Bahamas",                  "FIDE Rating", "",                                      ""),
    ("Barbados Chess Federation",                "BAR", "BB", "Barbados",                 "FIDE Rating", "",                                      ""),
    ("Belize Chess Federation",                  "BLZ", "BZ", "Belize",                   "FIDE Rating", "",                                      ""),
    ("Bolivian Chess Federation",                "BOL", "BO", "Bolivia",                  "FIDE Rating", "",                                      ""),
    ("Brazilian Chess Confederation",            "BRA", "BR", "Brazil",                   "FIDE Rating", "https://www.cbx.org.br",                 ""),
    ("Chilean Chess Federation",                 "CHI", "CL", "Chile",                    "FIDE Rating", "https://www.ajedreztotal.cl",             ""),
    ("Colombian Chess Federation",               "COL", "CO", "Colombia",                 "FIDE Rating", "https://www.fecodaz.org",                 ""),
    ("Costa Rica Chess Federation",              "CRC", "CR", "Costa Rica",               "FIDE Rating", "",                                      ""),
    ("Cuban Chess Federation",                   "CUB", "CU", "Cuba",                     "FIDE Rating", "",                                      ""),
    ("Dominican Republic Chess Federation",      "DOM", "DO", "Dominican Republic",       "FIDE Rating", "",                                      ""),
    ("Ecuadorian Chess Federation",              "ECU", "EC", "Ecuador",                  "FIDE Rating", "",                                      ""),
    ("El Salvador Chess Federation",             "ESA", "SV", "El Salvador",              "FIDE Rating", "",                                      ""),
    ("Guatemala Chess Federation",               "GUA", "GT", "Guatemala",                "FIDE Rating", "",                                      ""),
    ("Guyana Chess Federation",                  "GUY", "GY", "Guyana",                   "FIDE Rating", "",                                      ""),
    ("Haiti Chess Federation",                   "HAI", "HT", "Haiti",                    "FIDE Rating", "",                                      ""),
    ("Honduras Chess Federation",                "HON", "HN", "Honduras",                 "FIDE Rating", "",                                      ""),
    ("Jamaica Chess Federation",                 "JAM", "JM", "Jamaica",                  "FIDE Rating", "",                                      ""),
    ("Mexican Chess Federation",                 "MEX", "MX", "Mexico",                   "FIDE Rating", "https://www.fmaj.org",                   ""),
    ("Nicaragua Chess Federation",               "NCA", "NI", "Nicaragua",                "FIDE Rating", "",                                      ""),
    ("Panama Chess Federation",                  "PAN", "PA", "Panama",                   "FIDE Rating", "",                                      ""),
    ("Paraguay Chess Federation",                "PAR", "PY", "Paraguay",                 "FIDE Rating", "",                                      ""),
    ("Peruvian Chess Federation",                "PER", "PE", "Peru",                     "FIDE Rating", "https://www.ajedrezperu.com",             ""),
    ("Puerto Rico Chess Federation",             "PUR", "PR", "Puerto Rico",              "FIDE Rating", "",                                      ""),
    ("Suriname Chess Federation",                "SUR", "SR", "Suriname",                 "FIDE Rating", "",                                      ""),
    ("Trinidad & Tobago Chess Association",      "TTO", "TT", "Trinidad & Tobago",        "FIDE Rating", "",                                      ""),
    ("Uruguayan Chess Association",              "URU", "UY", "Uruguay",                  "FIDE Rating", "https://www.ajedrezuruguay.org",          ""),
    ("Venezuela Chess Federation",               "VEN", "VE", "Venezuela",                "FIDE Rating", "",                                      ""),

    # ── ASIA ────────────────────────────────────────────────────────────────
    ("Afghanistan Chess Federation",             "AFG", "AF", "Afghanistan",              "FIDE Rating", "",                                      ""),
    ("Bangladesh Chess Federation",              "BAN", "BD", "Bangladesh",               "FIDE Rating", "",                                      ""),
    ("Bhutan Chess Federation",                  "BHU", "BT", "Bhutan",                   "FIDE Rating", "",                                      ""),
    ("Cambodia Chess Federation",                "CAM", "KH", "Cambodia",                 "FIDE Rating", "",                                      ""),
    ("Chinese Chess Association",                "CHN", "CN", "China",                    "FIDE Rating", "https://www.chess.org.cn",               ""),
    ("Chinese Taipei Chess Association",         "TPE", "TW", "Chinese Taipei",           "FIDE Rating", "",                                      ""),
    ("Hong Kong Chess Federation",               "HKG", "HK", "Hong Kong",                "FIDE Rating", "https://www.hkchess.com",                "https://www.hkchess.com/events"),
    ("All India Chess Federation",               "IND", "IN", "India",                    "FIDE Rating", "https://www.aicf.in",                    "https://www.aicf.in/tournaments"),
    ("Indonesian Chess Association",             "INA", "ID", "Indonesia",                "FIDE Rating", "https://www.percasi.or.id",              ""),
    ("Japan Chess Association",                  "JPN", "JP", "Japan",                    "FIDE Rating", "https://www.jca.or.jp",                  ""),
    ("Kazakhstan Chess Federation",              "KAZ", "KZ", "Kazakhstan",               "FIDE Rating", "https://chess.kz",                       ""),
    ("Kyrgyzstan Chess Federation",              "KGZ", "KG", "Kyrgyzstan",               "FIDE Rating", "",                                      ""),
    ("Lao Chess Federation",                     "LAO", "LA", "Laos",                     "FIDE Rating", "",                                      ""),
    ("Macau Chess Federation",                   "MAC", "MO", "Macau",                    "FIDE Rating", "",                                      ""),
    ("Malaysian Chess Federation",               "MAS", "MY", "Malaysia",                 "FIDE Rating", "https://www.malaysiachess.org",           ""),
    ("Maldives Chess Federation",                "MDV", "MV", "Maldives",                 "FIDE Rating", "",                                      ""),
    ("Mongolian Chess Federation",               "MGL", "MN", "Mongolia",                 "FIDE Rating", "https://www.chess.mn",                   ""),
    ("Myanmar Chess Federation",                 "MYA", "MM", "Myanmar",                  "FIDE Rating", "",                                      ""),
    ("Nepal Chess Association",                  "NEP", "NP", "Nepal",                    "FIDE Rating", "",                                      ""),
    ("North Korea Chess Federation",             "PRK", "KP", "North Korea",              "FIDE Rating", "",                                      ""),
    ("Pakistan Chess Federation",                "PAK", "PK", "Pakistan",                 "FIDE Rating", "https://www.pakistanchessfederation.com", ""),
    ("National Chess Federation of Philippines", "PHI", "PH", "Philippines",              "FIDE Rating", "https://www.ncfp.com.ph",                ""),
    ("Singapore Chess Federation",               "SGP", "SG", "Singapore",                "FIDE Rating", "https://www.singaporechess.org.sg",      "https://www.singaporechess.org.sg/tournaments"),
    ("Korea Chess Federation",                   "KOR", "KR", "South Korea",              "FIDE Rating", "https://www.chess.or.kr",                ""),
    ("Sri Lanka Chess Federation",               "SRI", "LK", "Sri Lanka",                "FIDE Rating", "",                                      ""),
    ("Tajikistan Chess Federation",              "TJK", "TJ", "Tajikistan",               "FIDE Rating", "",                                      ""),
    ("Chess Association of Thailand",            "THA", "TH", "Thailand",                 "FIDE Rating", "https://www.thachess.com",               ""),
    ("Timor-Leste Chess Federation",             "TLS", "TL", "Timor-Leste",              "FIDE Rating", "",                                      ""),
    ("Turkmenistan Chess Federation",            "TKM", "TM", "Turkmenistan",             "FIDE Rating", "",                                      ""),
    ("Uzbekistan Chess Federation",              "UZB", "UZ", "Uzbekistan",               "FIDE Rating", "https://chess.uz",                       ""),
    ("Vietnam Chess Federation",                 "VIE", "VN", "Vietnam",                  "FIDE Rating", "https://www.chess.org.vn",               ""),

    # ── EUROPE ──────────────────────────────────────────────────────────────
    ("Albanian Chess Federation",                "ALB", "AL", "Albania",                  "FIDE Rating", "",                                      ""),
    ("Andorra Chess Federation",                 "AND", "AD", "Andorra",                  "FIDE Rating", "",                                      ""),
    ("Armenian Chess Federation",                "ARM", "AM", "Armenia",                  "FIDE Rating", "https://www.chessfed.am",               ""),
    ("Austrian Chess Federation",                "AUT", "AT", "Austria",                  "FIDE Rating", "https://www.schachbund.at",             ""),
    ("Azerbaijan Chess Federation",              "AZE", "AZ", "Azerbaijan",               "FIDE Rating", "https://www.azchess.az",                ""),
    ("Belarusian Chess Federation",              "BLR", "BY", "Belarus",                  "FIDE Rating", "https://chess.by",                      ""),
    ("Belgian Chess Federation",                 "BEL", "BE", "Belgium",                  "FIDE Rating", "https://www.frbe-kbsb-ksb.be",          ""),
    ("Chess Federation of Bosnia & Herzegovina", "BIH", "BA", "Bosnia & Herzegovina",     "FIDE Rating", "",                                      ""),
    ("Bulgarian Chess Federation",               "BUL", "BG", "Bulgaria",                 "FIDE Rating", "https://www.bgchess.com",               ""),
    ("Croatian Chess Federation",                "CRO", "HR", "Croatia",                  "FIDE Rating", "https://www.hrs.hr",                    ""),
    ("Cyprus Chess Federation",                  "CYP", "CY", "Cyprus",                   "FIDE Rating", "https://www.cyprussachess.org",         ""),
    ("Czech Chess Federation",                   "CZE", "CZ", "Czech Republic",           "FIDE Rating", "https://www.chess.cz",                  "https://www.chess.cz/souteze"),
    ("Danish Chess Federation",                  "DEN", "DK", "Denmark",                  "FIDE Rating", "https://www.skak.dk",                   ""),
    ("Chess Federation of England",              "ENG", "GB", "United Kingdom",           "ECF Rating",  "https://www.englishchess.org.uk",       "https://www.englishchess.org.uk/events/"),
    ("Estonian Chess Federation",                "EST", "EE", "Estonia",                  "FIDE Rating", "https://www.male.ee",                   ""),
    ("Faroe Islands Chess Federation",           "FAI", "FO", "Faroe Islands",            "FIDE Rating", "",                                      ""),
    ("Finnish Chess Federation",                 "FIN", "FI", "Finland",                  "FIDE Rating", "https://www.chess.fi",                  ""),
    ("Georgian Chess Federation",                "GEO", "GE", "Georgia",                  "FIDE Rating", "https://www.georgianfide.com",          ""),
    ("Gibraltar Chess Association",              "GIB", "GI", "Gibraltar",                "FIDE Rating", "https://www.gibchess.com",              ""),
    ("Hellenic Chess Federation",                "GRE", "GR", "Greece",                   "FIDE Rating", "https://www.chess.gr",                  ""),
    ("Hungarian Chess Federation",               "HUN", "HU", "Hungary",                  "FIDE Rating", "https://www.chess.hu",                  ""),
    ("Icelandic Chess Federation",               "ISL", "IS", "Iceland",                  "FIDE Rating", "https://www.skak.is",                   ""),
    ("Chess Ireland",                            "IRL", "IE", "Ireland",                  "FIDE Rating", "https://www.icu.ie",                    "https://www.icu.ie/tournaments"),
    ("Israel Chess Federation",                  "ISR", "IL", "Israel",                   "FIDE Rating", "https://www.chess.org.il",              ""),
    ("Italian Chess Federation",                 "ITA", "IT", "Italy",                    "FIDE Rating", "https://www.federscacchi.it",           ""),
    ("Kosovo Chess Federation",                  "KOS", "XK", "Kosovo",                   "FIDE Rating", "",                                      ""),
    ("Latvian Chess Federation",                 "LAT", "LV", "Latvia",                   "FIDE Rating", "https://www.sahafederacija.lv",         ""),
    ("Liechtenstein Chess Federation",           "LIE", "LI", "Liechtenstein",            "FIDE Rating", "",                                      ""),
    ("Lithuanian Chess Federation",              "LTU", "LT", "Lithuania",                "FIDE Rating", "https://www.chess.lt",                  ""),
    ("Luxembourg Chess Federation",              "LUX", "LU", "Luxembourg",               "FIDE Rating", "",                                      ""),
    ("Malta Chess Federation",                   "MLT", "MT", "Malta",                    "FIDE Rating", "https://www.maltachess.com",            ""),
    ("Moldovan Chess Federation",                "MDA", "MD", "Moldova",                  "FIDE Rating", "",                                      ""),
    ("Monaco Chess Club",                        "MON", "MC", "Monaco",                   "FIDE Rating", "",                                      ""),
    ("Montenegrin Chess Federation",             "MNE", "ME", "Montenegro",               "FIDE Rating", "",                                      ""),
    ("Royal Dutch Chess Federation",             "NED", "NL", "Netherlands",              "FIDE Rating", "https://www.schaakbond.nl",             ""),
    ("North Macedonia Chess Federation",         "MKD", "MK", "North Macedonia",          "FIDE Rating", "",                                      ""),
    ("Norwegian Chess Federation",               "NOR", "NO", "Norway",                   "FIDE Rating", "https://www.sjakk.no",                  ""),
    ("Polish Chess Federation",                  "POL", "PL", "Poland",                   "FIDE Rating", "https://www.pzszach.pl",                ""),
    ("Portuguese Chess Federation",              "POR", "PT", "Portugal",                 "FIDE Rating", "https://www.fpx.pt",                    ""),
    ("Romanian Chess Federation",                "ROU", "RO", "Romania",                  "FIDE Rating", "https://www.frsh.ro",                   ""),
    ("Russian Chess Federation",                 "RUS", "RU", "Russia",                   "FIDE Rating", "https://www.chess-russia.ru",           ""),
    ("San Marino Chess Federation",              "SMR", "SM", "San Marino",               "FIDE Rating", "",                                      ""),
    ("Scottish Chess",                           "SCO", "GB", "United Kingdom",           "FIDE Rating", "https://www.scottishchess.org.uk",      "https://www.scottishchess.org.uk/events"),
    ("Chess Federation of Serbia",               "SRB", "RS", "Serbia",                   "FIDE Rating", "https://www.sahsavez.rs",               ""),
    ("Slovak Chess Federation",                  "SVK", "SK", "Slovakia",                 "FIDE Rating", "https://www.chess.sk",                  ""),
    ("Chess Federation of Slovenia",             "SLO", "SI", "Slovenia",                 "FIDE Rating", "https://www.sahist.si",                 ""),
    ("Spanish Chess Federation",                 "ESP", "ES", "Spain",                    "FIDE Rating", "https://www.feda.es",                   "https://www.feda.es/torneos"),
    ("Swedish Chess Federation",                 "SWE", "SE", "Sweden",                   "FIDE Rating", "https://www.schack.se",                 ""),
    ("Swiss Chess Federation",                   "SUI", "CH", "Switzerland",              "FIDE Rating", "https://www.swisschess.ch",             "https://www.swisschess.ch/turniere.html"),
    ("Turkish Chess Federation",                 "TUR", "TR", "Turkey",                   "FIDE Rating", "https://www.tsf.org.tr",                ""),
    ("Ukrainian Chess Federation",               "UKR", "UA", "Ukraine",                  "FIDE Rating", "https://chess.ua",                      ""),
    ("Chess Wales",                              "WLS", "GB", "United Kingdom",           "FIDE Rating", "https://www.chesswales.org",            ""),

    # ── MIDDLE EAST ─────────────────────────────────────────────────────────
    ("Bahrain Chess Federation",                 "BRN", "BH", "Bahrain",                  "FIDE Rating", "",                                      ""),
    ("Iranian Chess Federation",                 "IRI", "IR", "Iran",                     "FIDE Rating", "https://www.irichess.ir",               ""),
    ("Iraqi Chess Federation",                   "IRQ", "IQ", "Iraq",                     "FIDE Rating", "",                                      ""),
    ("Jordan Chess Federation",                  "JOR", "JO", "Jordan",                   "FIDE Rating", "",                                      ""),
    ("Kuwait Chess Federation",                  "KUW", "KW", "Kuwait",                   "FIDE Rating", "",                                      ""),
    ("Lebanese Chess Federation",                "LIB", "LB", "Lebanon",                  "FIDE Rating", "",                                      ""),
    ("Oman Chess Association",                   "OMA", "OM", "Oman",                     "FIDE Rating", "",                                      ""),
    ("Palestinian Chess Federation",             "PLE", "PS", "Palestine",                "FIDE Rating", "",                                      ""),
    ("Qatar Chess Association",                  "QAT", "QA", "Qatar",                    "FIDE Rating", "https://www.qca-chess.qa",              ""),
    ("Saudi Arabian Chess Federation",           "KSA", "SA", "Saudi Arabia",             "FIDE Rating", "https://www.chess.org.sa",              ""),
    ("Syrian Chess Federation",                  "SYR", "SY", "Syria",                    "FIDE Rating", "",                                      ""),
    ("UAE Chess Federation",                     "UAE", "AE", "United Arab Emirates",     "FIDE Rating", "https://www.uaechess.ae",               ""),
    ("Yemen Chess Federation",                   "YEM", "YE", "Yemen",                    "FIDE Rating", "",                                      ""),

    # ── OCEANIA ─────────────────────────────────────────────────────────────
    ("Fiji Chess Federation",                    "FIJ", "FJ", "Fiji",                     "FIDE Rating", "",                                      ""),
    ("Guam Chess Federation",                    "GUM", "GU", "Guam",                     "FIDE Rating", "",                                      ""),
    ("New Zealand Chess Federation",             "NZL", "NZ", "New Zealand",              "FIDE Rating", "https://www.chess.org.nz",              "https://www.chess.org.nz/tournaments"),
    ("Papua New Guinea Chess Federation",        "PNG", "PG", "Papua New Guinea",         "FIDE Rating", "",                                      ""),
    ("Solomon Islands Chess Federation",         "SOL", "SB", "Solomon Islands",          "FIDE Rating", "",                                      ""),
    ("Vanuatu Chess Federation",                 "VAN", "VU", "Vanuatu",                  "FIDE Rating", "",                                      ""),
]


def _seed_all_federations(c) -> int:
    """Insert any FIDE member federation not yet in the table. Returns number added."""
    existing = {row[0] for row in c.execute("SELECT abbreviation FROM chess_federations").fetchall()}
    to_insert = [f for f in _ALL_FEDERATIONS if f[1] not in existing]
    if to_insert:
        c.executemany(
            "INSERT INTO chess_federations (name,abbreviation,country_code,country_name,rating_system,website_url,tournaments_url,active,display_order) "
            "VALUES (?,?,?,?,?,?,?,1,0)",
            to_insert,
        )
    return len(to_insert)
DB_FILE = os.environ.get("DB_FILE", "/data/mychessrating.db" if os.path.isdir("/data") else "mychessrating.db")

import hashlib
import hmac
import secrets

def _hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260000)
    return f"pbkdf2$sha256$260000${salt}${dk.hex()}"

def verify_password(plain: str, stored: str) -> bool:
    try:
        _, alg, iters, salt, dk_hex = stored.split("$")
        dk = hashlib.pbkdf2_hmac(alg, plain.encode(), salt.encode(), int(iters))
        return hmac.compare_digest(dk.hex(), dk_hex)
    except Exception:
        return False

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS tournaments (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        rounds INTEGER NOT NULL DEFAULT 5,
        system TEXT DEFAULT 'dutch',
        current_round INTEGER DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        entry_fee REAL DEFAULT 0,
        registration_open INTEGER DEFAULT 1
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS players (
        id INTEGER PRIMARY KEY,
        tournament_id INTEGER,
        name TEXT NOT NULL,
        uscf_id TEXT,
        rating INTEGER DEFAULT 0,
        email TEXT,
        score REAL DEFAULT 0.0,
        registered_at TEXT DEFAULT CURRENT_TIMESTAMP,
        status TEXT DEFAULT 'active',
        payment_status TEXT DEFAULT 'waived',
        payment_intent_id TEXT,
        requested_byes TEXT DEFAULT '[]',
        phone TEXT,
        FOREIGN KEY(tournament_id) REFERENCES tournaments(id)
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS results (
        id INTEGER PRIMARY KEY,
        tournament_id INTEGER,
        round INTEGER NOT NULL,
        white_id INTEGER,
        black_id INTEGER,
        result TEXT NOT NULL,
        FOREIGN KEY(tournament_id) REFERENCES tournaments(id)
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS player_round_scores (
        id INTEGER PRIMARY KEY,
        tournament_id INTEGER,
        player_id INTEGER,
        round INTEGER NOT NULL,
        score_after REAL DEFAULT 0.0,
        FOREIGN KEY(tournament_id) REFERENCES tournaments(id),
        FOREIGN KEY(player_id) REFERENCES players(id),
        UNIQUE(tournament_id, player_id, round)
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS uscf_members (
        uscf_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        rating INTEGER DEFAULT 0,
        state TEXT,
        expiry TEXT,
        fide_id TEXT
    )''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_uscf_members_name ON uscf_members(name)')

    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'viewer',
        status TEXT NOT NULL DEFAULT 'active',
        email TEXT,
        phone TEXT,
        uscf_id TEXT,
        fide_id TEXT,
        uscf_rating INTEGER,
        fide_rating INTEGER,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS verification_tokens (
        id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL,
        token TEXT NOT NULL,
        channel TEXT NOT NULL,
        contact TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS password_reset_tokens (
        id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL,
        token TEXT NOT NULL UNIQUE,
        expires_at TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS user_tournaments (
        id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        tournament_type TEXT DEFAULT 'standard',
        start_rating INTEGER,
        end_rating INTEGER,
        games_json TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        deleted_at TEXT DEFAULT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
    )''')
    try:
        c.execute("ALTER TABLE user_tournaments ADD COLUMN deleted_at TEXT DEFAULT NULL")
    except Exception:
        pass  # Column already exists

    c.execute('''CREATE TABLE IF NOT EXISTS featured_tournaments (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        subtitle TEXT,
        description TEXT,
        info_url TEXT,
        pairings_url TEXT,
        source TEXT DEFAULT 'manual',
        source_url TEXT,
        active INTEGER DEFAULT 1,
        display_order INTEGER DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS player_research (
        id INTEGER PRIMARY KEY,
        tournament_source TEXT,
        tournament_name TEXT,
        start_rank INTEGER DEFAULT 0,
        name TEXT NOT NULL,
        title TEXT,
        fide_id TEXT,
        fide_rating INTEGER DEFAULT 0,
        country TEXT,
        national_id TEXT,
        notes TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        created_by INTEGER,
        FOREIGN KEY(created_by) REFERENCES users(id)
    )''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_player_research_fide ON player_research(fide_id)')

    c.execute('''CREATE TABLE IF NOT EXISTS chess_federations (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        abbreviation TEXT DEFAULT '',
        country_code TEXT DEFAULT '',
        country_name TEXT DEFAULT '',
        rating_system TEXT DEFAULT '',
        website_url TEXT DEFAULT '',
        tournaments_url TEXT DEFAULT '',
        active INTEGER DEFAULT 1,
        display_order INTEGER DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS federation_cities (
        id INTEGER PRIMARY KEY,
        federation_id INTEGER NOT NULL,
        city_name TEXT NOT NULL,
        region TEXT DEFAULT '',
        active INTEGER DEFAULT 1,
        display_order INTEGER DEFAULT 0,
        FOREIGN KEY(federation_id) REFERENCES chess_federations(id)
    )''')

    # Seed default federations if table is empty
    if c.execute("SELECT COUNT(*) FROM chess_federations").fetchone()[0] == 0:
        federations = [
            ("US Chess Federation", "USCF", "US", "United States", "USCF Rating",
             "https://www.uschess.org", "https://www.uschess.org/tournaments/", 1, 10),
            ("Northwest Chess", "NWC", "US", "United States", "NW Chess Rating",
             "https://www.nwchess.com", "https://www.nwchess.com/tournaments/", 1, 20),
            ("FIDE", "FIDE", "INT", "International", "FIDE Rating",
             "https://www.fide.com", "https://www.fide.com/calendar", 1, 5),
            ("Chess Federation of Canada", "CFC", "CA", "Canada", "CFC Rating",
             "https://chess.ca", "https://chess.ca/en/tournaments/", 1, 30),
            ("English Chess Federation", "ECF", "GB", "United Kingdom", "ECF Rating",
             "https://www.englishchess.org.uk", "https://www.englishchess.org.uk/events/", 1, 40),
            ("Australian Chess Federation", "ACF", "AU", "Australia", "ACF Rating",
             "https://auschess.org.au", "https://auschess.org.au/tournaments/", 1, 50),
            ("Schachbund (Germany)", "DSB", "DE", "Germany", "DWZ Rating",
             "https://www.schachbund.de", "https://www.schachbund.de/turnierdatenbank.html", 1, 60),
            ("Fédération Française des Échecs", "FFE", "FR", "France", "FFE Elo",
             "https://www.echecs.asso.fr", "https://www.echecs.asso.fr/Calendrier.aspx", 1, 70),
        ]
        c.executemany(
            "INSERT INTO chess_federations (name,abbreviation,country_code,country_name,rating_system,website_url,tournaments_url,active,display_order) VALUES (?,?,?,?,?,?,?,?,?)",
            federations
        )
        # Seed cities for USCF (id will be 1)
        uscf_cities = [
            ("New York", "NY"), ("Los Angeles", "CA"), ("Chicago", "IL"),
            ("Houston", "TX"), ("Phoenix", "AZ"), ("Philadelphia", "PA"),
            ("San Antonio", "TX"), ("San Diego", "CA"), ("Dallas", "TX"),
            ("San Jose", "CA"), ("Austin", "TX"), ("Jacksonville", "FL"),
            ("Fort Worth", "TX"), ("Columbus", "OH"), ("Charlotte", "NC"),
            ("Indianapolis", "IN"), ("San Francisco", "CA"), ("Seattle", "WA"),
            ("Denver", "CO"), ("Nashville", "TN"), ("Boston", "MA"),
            ("Atlanta", "GA"), ("Miami", "FL"), ("Minneapolis", "MN"),
            ("St. Louis", "MO"), ("Las Vegas", "NV"), ("Portland", "OR"),
        ]
        uscf_id = c.execute("SELECT id FROM chess_federations WHERE abbreviation='USCF'").fetchone()[0]
        c.executemany(
            "INSERT INTO federation_cities (federation_id, city_name, region) VALUES (?, ?, ?)",
            [(uscf_id, city, region) for city, region in uscf_cities]
        )
        # Seed cities for NWC
        nwc_cities = [
            ("Seattle", "WA"), ("Portland", "OR"), ("Tacoma", "WA"),
            ("Spokane", "WA"), ("Eugene", "OR"), ("Bellevue", "WA"),
            ("Boise", "ID"), ("Olympia", "WA"), ("Bend", "OR"),
        ]
        nwc_id = c.execute("SELECT id FROM chess_federations WHERE abbreviation='NWC'").fetchone()[0]
        c.executemany(
            "INSERT INTO federation_cities (federation_id, city_name, region) VALUES (?, ?, ?)",
            [(nwc_id, city, region) for city, region in nwc_cities]
        )
        # Seed cities for CFC
        cfc_cities = [
            ("Toronto", "ON"), ("Montreal", "QC"), ("Vancouver", "BC"),
            ("Ottawa", "ON"), ("Calgary", "AB"), ("Edmonton", "AB"),
            ("Winnipeg", "MB"), ("Halifax", "NS"),
        ]
        cfc_id = c.execute("SELECT id FROM chess_federations WHERE abbreviation='CFC'").fetchone()[0]
        c.executemany(
            "INSERT INTO federation_cities (federation_id, city_name, region) VALUES (?, ?, ?)",
            [(cfc_id, city, region) for city, region in cfc_cities]
        )
        # Seed cities for ECF
        ecf_cities = [
            ("London", "England"), ("Manchester", "England"), ("Birmingham", "England"),
            ("Leeds", "England"), ("Bristol", "England"), ("Glasgow", "Scotland"),
            ("Edinburgh", "Scotland"), ("Cardiff", "Wales"),
        ]
        ecf_id = c.execute("SELECT id FROM chess_federations WHERE abbreviation='ECF'").fetchone()[0]
        c.executemany(
            "INSERT INTO federation_cities (federation_id, city_name, region) VALUES (?, ?, ?)",
            [(ecf_id, city, region) for city, region in ecf_cities]
        )
        # Seed cities for ACF
        acf_cities = [
            ("Sydney", "NSW"), ("Melbourne", "VIC"), ("Brisbane", "QLD"),
            ("Perth", "WA"), ("Adelaide", "SA"), ("Canberra", "ACT"),
        ]
        acf_id = c.execute("SELECT id FROM chess_federations WHERE abbreviation='ACF'").fetchone()[0]
        c.executemany(
            "INSERT INTO federation_cities (federation_id, city_name, region) VALUES (?, ?, ?)",
            [(acf_id, city, region) for city, region in acf_cities]
        )

    # Idempotent: add any FIDE member federations not yet present
    _seed_all_federations(c)

    # Migrations for existing DBs
    for sql in [
        "ALTER TABLE player_research ADD COLUMN start_rank INTEGER DEFAULT 0",
        "ALTER TABLE uscf_members ADD COLUMN fide_id TEXT",
        "ALTER TABLE players ADD COLUMN fide_id TEXT",
        "ALTER TABLE players ADD COLUMN expiry TEXT",
        "ALTER TABLE players ADD COLUMN fide_rating INTEGER DEFAULT 0",
        "ALTER TABLE players ADD COLUMN live_rating INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN status TEXT DEFAULT 'active'",
        "ALTER TABLE users ADD COLUMN email TEXT",
        "ALTER TABLE users ADD COLUMN phone TEXT",
        "ALTER TABLE tournaments ADD COLUMN entry_fee REAL DEFAULT 0",
        "ALTER TABLE tournaments ADD COLUMN registration_open INTEGER DEFAULT 1",
        "ALTER TABLE players ADD COLUMN status TEXT DEFAULT 'active'",
        "ALTER TABLE players ADD COLUMN payment_status TEXT DEFAULT 'waived'",
        "ALTER TABLE players ADD COLUMN payment_intent_id TEXT",
        "ALTER TABLE players ADD COLUMN requested_byes TEXT DEFAULT '[]'",
        "ALTER TABLE players ADD COLUMN phone TEXT",
        "ALTER TABLE users ADD COLUMN uscf_id TEXT",
        "ALTER TABLE users ADD COLUMN fide_id TEXT",
        "ALTER TABLE users ADD COLUMN uscf_rating INTEGER",
        "ALTER TABLE users ADD COLUMN fide_rating INTEGER",
        "ALTER TABLE users ADD COLUMN uscf_quick_rating INTEGER",
        "ALTER TABLE users ADD COLUMN uscf_blitz_rating INTEGER",
        "ALTER TABLE users ADD COLUMN fide_rapid_rating INTEGER",
        "ALTER TABLE users ADD COLUMN fide_blitz_rating INTEGER",
        "ALTER TABLE users ADD COLUMN player_name TEXT",
        "ALTER TABLE user_tournaments ADD COLUMN tournament_type TEXT DEFAULT 'standard'",
        "ALTER TABLE player_research ADD COLUMN lichess_id TEXT",
        "ALTER TABLE player_research ADD COLUMN chessdotcom_id TEXT",
        "ALTER TABLE users ADD COLUMN country_code TEXT DEFAULT ''",
        "ALTER TABLE users ADD COLUMN city TEXT DEFAULT ''",
        "ALTER TABLE users ADD COLUMN federation_id INTEGER",
        "ALTER TABLE featured_tournaments ADD COLUMN country_code TEXT DEFAULT ''",
    ]:
        try:
            c.execute(sql)
        except Exception:
            pass

    conn.commit()
    conn.close()

init_db()


# ---------------------------------------------------------------------------
# User / auth functions
# ---------------------------------------------------------------------------

def create_user(username: str, password: str, role: str = "viewer", status: str = "active") -> int:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT INTO users (username, password_hash, role, status) VALUES (?, ?, ?, ?)",
              (username.strip(), _hash_password(password), role, status))
    uid = c.lastrowid
    conn.commit()
    conn.close()
    return uid

def create_pending_user(username: str, password: str, email: str = None, phone: str = None) -> int:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        "INSERT INTO users (username, password_hash, role, status, email, phone) VALUES (?, ?, 'viewer', 'pending', ?, ?)",
        (username.strip(), _hash_password(password), email, phone)
    )
    uid = c.lastrowid
    conn.commit()
    conn.close()
    return uid

def get_user_by_username(username: str) -> Optional[Dict]:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, username, password_hash, role, status, email, phone FROM users WHERE username=?", (username.strip(),))
    row = c.fetchone()
    conn.close()
    return {"id": row[0], "username": row[1], "password_hash": row[2], "role": row[3],
            "status": row[4], "email": row[5], "phone": row[6]} if row else None

def get_user_by_email(email: str) -> Optional[Dict]:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, username, status FROM users WHERE email=?", (email.strip(),))
    row = c.fetchone()
    conn.close()
    return {"id": row[0], "username": row[1], "status": row[2]} if row else None

def get_user_by_phone(phone: str) -> Optional[Dict]:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, username FROM users WHERE phone=?", (phone.strip(),))
    row = c.fetchone()
    conn.close()
    return {"id": row[0], "username": row[1]} if row else None

def create_verification_token(user_id: int, channel: str, contact: str) -> str:
    import random
    from datetime import datetime, timedelta
    token = f"{random.randint(0, 999999):06d}"
    expires = (datetime.utcnow() + timedelta(minutes=10)).isoformat()
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM verification_tokens WHERE user_id=?", (user_id,))
    c.execute("INSERT INTO verification_tokens (user_id, token, channel, contact, expires_at) VALUES (?, ?, ?, ?, ?)",
              (user_id, token, channel, contact, expires))
    conn.commit()
    conn.close()
    return token

def check_and_consume_token(user_id: int, token: str) -> bool:
    from datetime import datetime
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, expires_at FROM verification_tokens WHERE user_id=? AND token=?", (user_id, token))
    row = c.fetchone()
    if not row or datetime.utcnow().isoformat() > row[1]:
        conn.close()
        return False
    c.execute("DELETE FROM verification_tokens WHERE id=?", (row[0],))
    conn.commit()
    conn.close()
    return True

def activate_user(user_id: int):
    conn = sqlite3.connect(DB_FILE)
    conn.execute("UPDATE users SET status='active' WHERE id=?", (user_id,))
    conn.commit()
    conn.close()

def list_users() -> List[Dict]:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, username, role, status, created_at, email, phone FROM users ORDER BY created_at")
    rows = c.fetchall()
    conn.close()
    return [{"id": r[0], "username": r[1], "role": r[2], "status": r[3], "created_at": r[4], "email": r[5], "phone": r[6]} for r in rows]

def delete_user(uid: int):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM users WHERE id=?", (uid,))
    conn.commit()
    conn.close()

def update_user_password(uid: int, new_password: str):
    conn = sqlite3.connect(DB_FILE)
    conn.execute("UPDATE users SET password_hash=? WHERE id=?", (_hash_password(new_password), uid))
    conn.commit()
    conn.close()

def update_user_info(uid: int, username: str, email: Optional[str], phone: Optional[str], role: str, status: str):
    conn = sqlite3.connect(DB_FILE)
    conn.execute(
        "UPDATE users SET username=?, email=?, phone=?, role=?, status=? WHERE id=?",
        (username.strip(), email or None, phone or None, role, status, uid)
    )
    conn.commit()
    conn.close()

def create_password_reset_token(user_id: int) -> str:
    from datetime import timedelta
    token = secrets.token_urlsafe(32)
    expires = (datetime.utcnow() + timedelta(hours=1)).isoformat()
    conn = sqlite3.connect(DB_FILE)
    conn.execute("DELETE FROM password_reset_tokens WHERE user_id=?", (user_id,))
    conn.execute("INSERT INTO password_reset_tokens (user_id, token, expires_at) VALUES (?, ?, ?)",
                 (user_id, token, expires))
    conn.commit()
    conn.close()
    return token

def check_and_consume_reset_token(token: str) -> Optional[int]:
    """Returns user_id if token is valid, None otherwise. Always deletes the token."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT user_id, expires_at FROM password_reset_tokens WHERE token=?", (token,))
    row = c.fetchone()
    if not row:
        conn.close()
        return None
    user_id, expires_at = row
    conn.execute("DELETE FROM password_reset_tokens WHERE token=?", (token,))
    conn.commit()
    conn.close()
    if datetime.utcnow().isoformat() > expires_at:
        return None
    return user_id

def get_user_profile(uid: int) -> Optional[Dict]:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        "SELECT id, username, role, email, phone, uscf_id, fide_id, uscf_rating, fide_rating, "
        "uscf_quick_rating, uscf_blitz_rating, fide_rapid_rating, fide_blitz_rating, player_name FROM users WHERE id=?",
        (uid,)
    )
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    cols = ["id", "username", "role", "email", "phone", "uscf_id", "fide_id", "uscf_rating", "fide_rating",
            "uscf_quick_rating", "uscf_blitz_rating", "fide_rapid_rating", "fide_blitz_rating", "player_name"]
    return dict(zip(cols, row))

def update_user_profile(uid: int, uscf_id: Optional[str], fide_id: Optional[str],
                         uscf_rating: Optional[int], fide_rating: Optional[int],
                         uscf_quick_rating: Optional[int] = None, uscf_blitz_rating: Optional[int] = None,
                         fide_rapid_rating: Optional[int] = None, fide_blitz_rating: Optional[int] = None,
                         player_name: Optional[str] = None):
    conn = sqlite3.connect(DB_FILE)
    conn.execute(
        "UPDATE users SET uscf_id=?, fide_id=?, uscf_rating=?, fide_rating=?, "
        "uscf_quick_rating=?, uscf_blitz_rating=?, fide_rapid_rating=?, fide_blitz_rating=?, player_name=? WHERE id=?",
        (uscf_id, fide_id, uscf_rating, fide_rating,
         uscf_quick_rating, uscf_blitz_rating, fide_rapid_rating, fide_blitz_rating, player_name, uid)
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# User saved tournaments (USCF calculator)
# ---------------------------------------------------------------------------

def save_user_tournament(user_id: int, name: str, start_rating: Optional[int],
                          end_rating: Optional[int], games_json: str,
                          tournament_type: str = 'standard') -> int:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        "INSERT INTO user_tournaments (user_id, name, tournament_type, start_rating, end_rating, games_json) VALUES (?,?,?,?,?,?)",
        (user_id, name.strip(), tournament_type, start_rating, end_rating, games_json)
    )
    new_id = c.lastrowid
    conn.commit()
    conn.close()
    return new_id

def list_user_tournaments(user_id: int) -> List[Dict]:
    conn = sqlite3.connect(DB_FILE)
    rows = conn.execute(
        "SELECT id, name, tournament_type, start_rating, end_rating, games_json, created_at "
        "FROM user_tournaments WHERE user_id=? AND deleted_at IS NULL ORDER BY created_at DESC",
        (user_id,)
    ).fetchall()
    conn.close()
    cols = ["id", "name", "tournament_type", "start_rating", "end_rating", "games_json", "created_at"]
    return [dict(zip(cols, r)) for r in rows]

def list_deleted_user_tournaments(user_id: int) -> List[Dict]:
    conn = sqlite3.connect(DB_FILE)
    rows = conn.execute(
        "SELECT id, name, tournament_type, start_rating, end_rating, games_json, created_at, deleted_at "
        "FROM user_tournaments WHERE user_id=? AND deleted_at IS NOT NULL ORDER BY deleted_at DESC",
        (user_id,)
    ).fetchall()
    conn.close()
    cols = ["id", "name", "tournament_type", "start_rating", "end_rating", "games_json", "created_at", "deleted_at"]
    return [dict(zip(cols, r)) for r in rows]

def update_user_contact(uid: int, email: Optional[str], phone: Optional[str]):
    conn = sqlite3.connect(DB_FILE)
    conn.execute("UPDATE users SET email=?, phone=? WHERE id=?",
                 (email or None, phone or None, uid))
    conn.commit()
    conn.close()

def update_user_tournament(tournament_id: int, user_id: int, name: str,
                            start_rating: Optional[int], end_rating: Optional[int],
                            games_json: str, tournament_type: str = 'standard') -> bool:
    conn = sqlite3.connect(DB_FILE)
    cur = conn.execute(
        "UPDATE user_tournaments SET name=?, tournament_type=?, start_rating=?, end_rating=?, games_json=? "
        "WHERE id=? AND user_id=?",
        (name.strip(), tournament_type, start_rating, end_rating, games_json, tournament_id, user_id)
    )
    conn.commit()
    conn.close()
    return cur.rowcount > 0

def delete_user_tournament(tournament_id: int, user_id: int) -> bool:
    conn = sqlite3.connect(DB_FILE)
    cur = conn.execute(
        "UPDATE user_tournaments SET deleted_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=? AND deleted_at IS NULL",
        (tournament_id, user_id)
    )
    conn.commit()
    conn.close()
    return cur.rowcount > 0

def undelete_user_tournament(tournament_id: int, user_id: int) -> bool:
    conn = sqlite3.connect(DB_FILE)
    cur = conn.execute(
        "UPDATE user_tournaments SET deleted_at=NULL WHERE id=? AND user_id=? AND deleted_at IS NOT NULL",
        (tournament_id, user_id)
    )
    conn.commit()
    conn.close()
    return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Featured Tournaments (admin-managed)
# ---------------------------------------------------------------------------

def add_featured_tournament(name: str, subtitle: str = None, description: str = None,
                             info_url: str = None, pairings_url: str = None,
                             source: str = 'manual', source_url: str = None,
                             display_order: int = 0, country_code: str = '') -> int:
    conn = sqlite3.connect(DB_FILE)
    cur = conn.execute(
        "INSERT INTO featured_tournaments (name, subtitle, description, info_url, pairings_url, source, source_url, display_order, country_code) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (name.strip(), subtitle, description, info_url, pairings_url, source, source_url, display_order, country_code or '')
    )
    fid = cur.lastrowid
    conn.commit()
    conn.close()
    return fid

_FT_COLS = ["id", "name", "subtitle", "description", "info_url", "pairings_url",
            "source", "source_url", "active", "display_order", "created_at", "country_code"]

def list_featured_tournaments(active_only: bool = False, country_code: str = None) -> List[Dict]:
    conn = sqlite3.connect(DB_FILE)
    conditions = []
    if active_only:
        conditions.append("active=1")
    if country_code:
        conditions.append(f"(country_code='' OR country_code='{country_code}')")
    sql = "SELECT id, name, subtitle, description, info_url, pairings_url, source, source_url, active, display_order, created_at, COALESCE(country_code,'') FROM featured_tournaments"
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY display_order ASC, created_at DESC"
    rows = conn.execute(sql).fetchall()
    conn.close()
    return [dict(zip(_FT_COLS, r)) for r in rows]

def get_user_location(user_id: int) -> Dict:
    conn = sqlite3.connect(DB_FILE)
    row = conn.execute(
        "SELECT COALESCE(country_code,''), COALESCE(city,''), federation_id FROM users WHERE id=?",
        (user_id,)
    ).fetchone()
    conn.close()
    if not row:
        return {"country_code": "", "city": "", "federation_id": None}
    return {"country_code": row[0], "city": row[1], "federation_id": row[2]}

def list_featured_tournaments_for_user(user_id: int) -> List[Dict]:
    loc = get_user_location(user_id)
    return list_featured_tournaments(active_only=True, country_code=loc["country_code"] or None)

def update_featured_tournament(fid: int, **kwargs) -> bool:
    allowed = {"name", "subtitle", "description", "info_url", "pairings_url", "source", "source_url", "active", "display_order", "country_code"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return False
    set_clause = ", ".join(f"{k}=?" for k in fields)
    conn = sqlite3.connect(DB_FILE)
    cur = conn.execute(f"UPDATE featured_tournaments SET {set_clause} WHERE id=?",
                       list(fields.values()) + [fid])
    conn.commit()
    conn.close()
    return cur.rowcount > 0

def delete_featured_tournament(fid: int) -> bool:
    conn = sqlite3.connect(DB_FILE)
    cur = conn.execute("DELETE FROM featured_tournaments WHERE id=?", (fid,))
    conn.commit()
    conn.close()
    return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Player Research
# ---------------------------------------------------------------------------

def upsert_research_players(players: list, tournament_source: str, tournament_name: str, created_by: int) -> int:
    """Insert players from a tournament import; skip duplicates (same fide_id + tournament_source)."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    inserted = 0
    for i, p in enumerate(players, start=1):
        fide_id = p.get('fide_id') or None
        if fide_id:
            exists = c.execute(
                "SELECT id FROM player_research WHERE fide_id=? AND tournament_source=?",
                (fide_id, tournament_source)
            ).fetchone()
            if exists:
                continue
        c.execute(
            "INSERT INTO player_research (tournament_source, tournament_name, start_rank, name, title, fide_id, fide_rating, country, national_id, created_by) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (tournament_source, tournament_name, p.get('start_rank', i), p.get('name',''), p.get('title',''),
             fide_id, p.get('fide_rating', 0) or 0, p.get('country',''), p.get('national_id',''), created_by)
        )
        inserted += 1
    conn.commit()
    conn.close()
    return inserted

def list_research_players(search: str = '', limit: int = 200) -> List[Dict]:
    conn = sqlite3.connect(DB_FILE)
    if search:
        rows = conn.execute(
            "SELECT id, tournament_name, tournament_source, start_rank, name, title, fide_id, fide_rating, country, national_id, notes, created_at "
            "FROM player_research WHERE name LIKE ? OR fide_id LIKE ? ORDER BY fide_rating DESC, name LIMIT ?",
            (f'%{search}%', f'%{search}%', limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, tournament_name, tournament_source, start_rank, name, title, fide_id, fide_rating, country, national_id, notes, created_at "
            "FROM player_research ORDER BY fide_rating DESC, name LIMIT ?",
            (limit,)
        ).fetchall()
    conn.close()
    cols = ['id','tournament_name','tournament_source','start_rank','name','title','fide_id','fide_rating','country','national_id','notes','created_at']
    return [dict(zip(cols, r)) for r in rows]

def get_research_players_by_rank_range(tournament_source: str, rank_lo: int, rank_hi: int) -> List[Dict]:
    conn = sqlite3.connect(DB_FILE)
    rows = conn.execute(
        "SELECT id, tournament_name, tournament_source, start_rank, name, title, fide_id, fide_rating, country, national_id, lichess_id, chessdotcom_id "
        "FROM player_research WHERE tournament_source=? AND start_rank BETWEEN ? AND ? ORDER BY start_rank",
        (tournament_source, rank_lo, rank_hi)
    ).fetchall()
    conn.close()
    cols = ['id','tournament_name','tournament_source','start_rank','name','title','fide_id','fide_rating','country','national_id','lichess_id','chessdotcom_id']
    return [dict(zip(cols, r)) for r in rows]

def get_research_player_count(tournament_source: str) -> int:
    conn = sqlite3.connect(DB_FILE)
    n = conn.execute("SELECT COUNT(*) FROM player_research WHERE tournament_source=?", (tournament_source,)).fetchone()[0]
    conn.close()
    return n

def delete_research_player(rid: int) -> bool:
    conn = sqlite3.connect(DB_FILE)
    cur = conn.execute("DELETE FROM player_research WHERE id=?", (rid,))
    conn.commit()
    conn.close()
    return cur.rowcount > 0

def delete_research_by_source(tournament_source: str) -> int:
    conn = sqlite3.connect(DB_FILE)
    cur = conn.execute("DELETE FROM player_research WHERE tournament_source=?", (tournament_source,))
    n = cur.rowcount
    conn.commit()
    conn.close()
    return n

def list_research_sources() -> List[Dict]:
    conn = sqlite3.connect(DB_FILE)
    rows = conn.execute(
        "SELECT tournament_source, tournament_name, COUNT(*) as player_count, MAX(created_at) as imported_at "
        "FROM player_research GROUP BY tournament_source ORDER BY imported_at DESC"
    ).fetchall()
    conn.close()
    return [{'tournament_source': r[0], 'tournament_name': r[1], 'player_count': r[2], 'imported_at': r[3]} for r in rows]


# ---------------------------------------------------------------------------
# ChessBase player data / PGN storage
# ---------------------------------------------------------------------------

def init_round_results():
    conn = sqlite3.connect(DB_FILE)
    conn.execute('''CREATE TABLE IF NOT EXISTS tournament_round_results (
        id INTEGER PRIMARY KEY,
        tournament_source TEXT NOT NULL,
        round INTEGER NOT NULL,
        board_no INTEGER DEFAULT 0,
        white_start_no INTEGER DEFAULT 0,
        white_name TEXT DEFAULT '',
        black_start_no INTEGER DEFAULT 0,
        black_name TEXT DEFAULT '',
        result TEXT DEFAULT '*',
        round_url TEXT DEFAULT '',
        imported_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_rr ON tournament_round_results(tournament_source, round)')
    # migrate: add round_url if missing
    try:
        conn.execute('ALTER TABLE tournament_round_results ADD COLUMN round_url TEXT DEFAULT ""')
    except Exception:
        pass
    conn.commit()
    conn.close()

init_round_results()

def store_round_results(tournament_source: str, round_num: int, results: list, round_url: str = ''):
    conn = sqlite3.connect(DB_FILE)
    conn.execute("DELETE FROM tournament_round_results WHERE tournament_source=? AND round=?",
                 (tournament_source, round_num))
    for r in results:
        conn.execute(
            "INSERT INTO tournament_round_results "
            "(tournament_source, round, board_no, white_start_no, white_name, black_start_no, black_name, result, round_url) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (tournament_source, round_num,
             r.get('board_no', 0), r.get('white_no', 0), r.get('white_name', ''),
             r.get('black_no', 0), r.get('black_name', ''), r.get('result', '*'), round_url)
        )
    conn.commit()
    conn.close()


def get_round_urls(tournament_source: str) -> dict:
    """Return {round_num: url} for all imported rounds."""
    conn = sqlite3.connect(DB_FILE)
    rows = conn.execute(
        "SELECT round, round_url FROM tournament_round_results "
        "WHERE tournament_source=? AND round_url != '' GROUP BY round",
        (tournament_source,)
    ).fetchall()
    conn.close()
    return {r[0]: r[1] for r in rows}

def get_round_results(tournament_source: str, round_num: int) -> List[Dict]:
    conn = sqlite3.connect(DB_FILE)
    rows = conn.execute(
        "SELECT board_no, white_start_no, white_name, black_start_no, black_name, result "
        "FROM tournament_round_results WHERE tournament_source=? AND round=? ORDER BY board_no",
        (tournament_source, round_num)
    ).fetchall()
    conn.close()
    cols = ['board_no', 'white_start_no', 'white_name', 'black_start_no', 'black_name', 'result']
    return [dict(zip(cols, r)) for r in rows]

def list_imported_rounds(tournament_source: str) -> List[int]:
    conn = sqlite3.connect(DB_FILE)
    rows = conn.execute(
        "SELECT DISTINCT round FROM tournament_round_results WHERE tournament_source=? ORDER BY round",
        (tournament_source,)
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]

def get_player_scores_after_round(tournament_source: str, through_round: int) -> Dict[int, float]:
    """Return {start_no: cumulative_score} after all rounds up to through_round."""
    conn = sqlite3.connect(DB_FILE)
    rows = conn.execute(
        "SELECT white_start_no, black_start_no, result FROM tournament_round_results "
        "WHERE tournament_source=? AND round<=? AND white_start_no > 0 AND black_start_no > 0",
        (tournament_source, through_round)
    ).fetchall()
    conn.close()
    scores: Dict[int, float] = {}
    for w_no, b_no, result in rows:
        if '1 - 0' in result or result == '1-0' or result == '1:0':
            scores[w_no] = scores.get(w_no, 0) + 1.0
            scores[b_no] = scores.get(b_no, 0)
        elif '0 - 1' in result or result == '0-1' or result == '0:1':
            scores[w_no] = scores.get(w_no, 0)
            scores[b_no] = scores.get(b_no, 0) + 1.0
        elif '½' in result or '1/2' in result:
            scores[w_no] = scores.get(w_no, 0) + 0.5
            scores[b_no] = scores.get(b_no, 0) + 0.5
        else:
            scores.setdefault(w_no, 0)
            scores.setdefault(b_no, 0)
    return scores

def init_standings():
    conn = sqlite3.connect(DB_FILE)
    conn.execute('''CREATE TABLE IF NOT EXISTS tournament_standings (
        id INTEGER PRIMARY KEY,
        tournament_source TEXT NOT NULL,
        round INTEGER NOT NULL,
        start_no INTEGER DEFAULT 0,
        name TEXT DEFAULT '',
        pts REAL DEFAULT 0,
        standings_url TEXT DEFAULT '',
        imported_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_st ON tournament_standings(tournament_source, round)')
    conn.commit()
    conn.close()

init_standings()


def store_standings(tournament_source: str, round_num: int, standings: list, standings_url: str = ''):
    conn = sqlite3.connect(DB_FILE)
    conn.execute("DELETE FROM tournament_standings WHERE tournament_source=? AND round=?",
                 (tournament_source, round_num))
    for s in standings:
        conn.execute(
            "INSERT INTO tournament_standings (tournament_source, round, start_no, name, pts, standings_url) VALUES (?,?,?,?,?,?)",
            (tournament_source, round_num, s['start_no'], s['name'], s['pts'], standings_url)
        )
    conn.commit()
    conn.close()


def get_round_standings(tournament_source: str, round_num: int) -> List[Dict]:
    conn = sqlite3.connect(DB_FILE)
    rows = conn.execute(
        "SELECT start_no, name, pts FROM tournament_standings WHERE tournament_source=? AND round=? ORDER BY pts DESC, start_no ASC",
        (tournament_source, round_num)
    ).fetchall()
    conn.close()
    return [{'start_no': r[0], 'name': r[1], 'pts': r[2]} for r in rows]


def list_imported_standings(tournament_source: str) -> List[Dict]:
    """Return [{round, standings_url}] for all imported standings."""
    conn = sqlite3.connect(DB_FILE)
    rows = conn.execute(
        "SELECT round, standings_url FROM tournament_standings WHERE tournament_source=? GROUP BY round ORDER BY round",
        (tournament_source,)
    ).fetchall()
    conn.close()
    return [{'round': r[0], 'standings_url': r[1]} for r in rows]


def get_research_players_by_start_nos(tournament_source: str, start_nos: List[int]) -> List[Dict]:
    if not start_nos:
        return []
    placeholders = ','.join('?' * len(start_nos))
    conn = sqlite3.connect(DB_FILE)
    rows = conn.execute(
        f"SELECT id, tournament_name, tournament_source, start_rank, name, title, fide_id, fide_rating, country, lichess_id, chessdotcom_id "
        f"FROM player_research WHERE tournament_source=? AND start_rank IN ({placeholders}) ORDER BY start_rank",
        [tournament_source] + list(start_nos)
    ).fetchall()
    conn.close()
    cols = ['id','tournament_name','tournament_source','start_rank','name','title','fide_id','fide_rating','country','lichess_id','chessdotcom_id']
    # Return in the same order as start_nos
    by_rank = {dict(zip(cols, r))['start_rank']: dict(zip(cols, r)) for r in rows}
    return [by_rank[n] for n in start_nos if n in by_rank]


def init_player_chessbase():
    conn = sqlite3.connect(DB_FILE)
    conn.execute('''CREATE TABLE IF NOT EXISTS player_chessbase (
        id INTEGER PRIMARY KEY,
        fide_id TEXT UNIQUE,
        player_name TEXT,
        chessbase_player_id TEXT,
        chessbase_url TEXT,
        game_count INTEGER DEFAULT 0,
        pgn_data TEXT,
        raw_info TEXT,
        fetched_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    conn.commit()
    conn.close()

init_player_chessbase()

def upsert_player_chessbase(fide_id: str, player_name: str, chessbase_player_id: str,
                             chessbase_url: str, game_count: int, pgn_data: str, raw_info: str):
    conn = sqlite3.connect(DB_FILE)
    conn.execute(
        "INSERT OR REPLACE INTO player_chessbase "
        "(fide_id, player_name, chessbase_player_id, chessbase_url, game_count, pgn_data, raw_info, fetched_at) "
        "VALUES (?,?,?,?,?,?,?,CURRENT_TIMESTAMP)",
        (fide_id, player_name, chessbase_player_id, chessbase_url, game_count, pgn_data, raw_info)
    )
    conn.commit()
    conn.close()

def get_player_chessbase(fide_id: str) -> Optional[Dict]:
    conn = sqlite3.connect(DB_FILE)
    row = conn.execute(
        "SELECT fide_id, player_name, chessbase_player_id, chessbase_url, game_count, pgn_data, raw_info, fetched_at "
        "FROM player_chessbase WHERE fide_id=?", (fide_id,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    cols = ['fide_id','player_name','chessbase_player_id','chessbase_url','game_count','pgn_data','raw_info','fetched_at']
    return dict(zip(cols, row))

def get_research_player_fide_id_by_user(tournament_source: str, fide_id: str) -> Optional[Dict]:
    """Find a player in a tournament by FIDE ID and return their entry."""
    conn = sqlite3.connect(DB_FILE)
    row = conn.execute(
        "SELECT id, start_rank, name, title, fide_id, fide_rating, country "
        "FROM player_research WHERE tournament_source=? AND fide_id=?",
        (tournament_source, fide_id)
    ).fetchone()
    conn.close()
    if not row:
        return None
    return dict(zip(['id','start_rank','name','title','fide_id','fide_rating','country'], row))


def update_research_player_links(player_id: int, lichess_id: str, chessdotcom_id: str):
    conn = sqlite3.connect(DB_FILE)
    conn.execute(
        "UPDATE player_research SET lichess_id=?, chessdotcom_id=? WHERE id=?",
        (lichess_id or None, chessdotcom_id or None, player_id)
    )
    conn.commit()
    conn.close()

def get_research_player_by_id(player_id: int) -> Optional[Dict]:
    conn = sqlite3.connect(DB_FILE)
    row = conn.execute(
        "SELECT id, start_rank, name, title, fide_id, fide_rating, country, lichess_id, chessdotcom_id "
        "FROM player_research WHERE id=?", (player_id,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    return dict(zip(['id','start_rank','name','title','fide_id','fide_rating','country','lichess_id','chessdotcom_id'], row))


# ---------------------------------------------------------------------------
# PGN Database
# ---------------------------------------------------------------------------

def init_pgn_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS pgn_organizers (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        lichess_id TEXT,
        chesscom_id TEXT,
        notes TEXT,
        created_by INTEGER REFERENCES users(id),
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS pgn_sources (
        id INTEGER PRIMARY KEY,
        organizer_id INTEGER REFERENCES pgn_organizers(id),
        source_type TEXT NOT NULL,
        source_url TEXT NOT NULL,
        tournament_name TEXT,
        round_num INTEGER,
        round_name TEXT,
        lichess_tour_id TEXT,
        lichess_round_id TEXT,
        is_live INTEGER DEFAULT 0,
        auto_refresh INTEGER DEFAULT 1,
        last_fetched TEXT,
        game_count INTEGER DEFAULT 0,
        auto_discovered INTEGER DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_pgn_sources_tour ON pgn_sources(lichess_tour_id)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_pgn_sources_round ON pgn_sources(lichess_round_id)')
    c.execute('''CREATE TABLE IF NOT EXISTS pgn_games (
        id INTEGER PRIMARY KEY,
        source_id INTEGER REFERENCES pgn_sources(id),
        event TEXT,
        site TEXT,
        date TEXT,
        round TEXT,
        white TEXT,
        black TEXT,
        result TEXT,
        white_elo INTEGER,
        black_elo INTEGER,
        eco TEXT,
        opening TEXT,
        time_control TEXT,
        raw_pgn TEXT NOT NULL,
        game_hash TEXT UNIQUE NOT NULL,
        research_white_id INTEGER,
        research_black_id INTEGER,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_pgn_games_white ON pgn_games(white COLLATE NOCASE)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_pgn_games_black ON pgn_games(black COLLATE NOCASE)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_pgn_games_event ON pgn_games(event)')
    conn.commit()
    conn.close()

init_pgn_db()

# -- Organizers --

def list_pgn_organizers() -> List[Dict]:
    conn = sqlite3.connect(DB_FILE)
    rows = conn.execute(
        "SELECT id, name, lichess_id, chesscom_id, notes, created_at FROM pgn_organizers ORDER BY name"
    ).fetchall()
    conn.close()
    return [dict(zip(['id','name','lichess_id','chesscom_id','notes','created_at'], r)) for r in rows]

def add_pgn_organizer(name: str, lichess_id: str, chesscom_id: str, notes: str, created_by: int) -> int:
    conn = sqlite3.connect(DB_FILE)
    cur = conn.execute(
        "INSERT INTO pgn_organizers (name, lichess_id, chesscom_id, notes, created_by) VALUES (?,?,?,?,?)",
        (name, lichess_id or None, chesscom_id or None, notes or None, created_by)
    )
    oid = cur.lastrowid
    conn.commit()
    conn.close()
    return oid

def delete_pgn_organizer(oid: int):
    conn = sqlite3.connect(DB_FILE)
    conn.execute("DELETE FROM pgn_organizers WHERE id=?", (oid,))
    conn.commit()
    conn.close()

# -- Sources --

def list_pgn_sources(organizer_id: int = None) -> List[Dict]:
    conn = sqlite3.connect(DB_FILE)
    if organizer_id:
        rows = conn.execute(
            "SELECT s.id, s.organizer_id, o.name as organizer_name, s.source_type, s.source_url, "
            "s.tournament_name, s.round_num, s.round_name, s.lichess_tour_id, s.lichess_round_id, "
            "s.is_live, s.auto_refresh, s.last_fetched, s.game_count, s.auto_discovered, s.created_at "
            "FROM pgn_sources s LEFT JOIN pgn_organizers o ON o.id=s.organizer_id "
            "WHERE s.organizer_id=? ORDER BY s.tournament_name, s.round_num",
            (organizer_id,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT s.id, s.organizer_id, o.name as organizer_name, s.source_type, s.source_url, "
            "s.tournament_name, s.round_num, s.round_name, s.lichess_tour_id, s.lichess_round_id, "
            "s.is_live, s.auto_refresh, s.last_fetched, s.game_count, s.auto_discovered, s.created_at "
            "FROM pgn_sources s LEFT JOIN pgn_organizers o ON o.id=s.organizer_id "
            "ORDER BY s.created_at DESC"
        ).fetchall()
    conn.close()
    cols = ['id','organizer_id','organizer_name','source_type','source_url','tournament_name',
            'round_num','round_name','lichess_tour_id','lichess_round_id',
            'is_live','auto_refresh','last_fetched','game_count','auto_discovered','created_at']
    return [dict(zip(cols, r)) for r in rows]

def get_pgn_source(sid: int) -> Optional[Dict]:
    conn = sqlite3.connect(DB_FILE)
    row = conn.execute(
        "SELECT id, organizer_id, source_type, source_url, tournament_name, round_num, round_name, "
        "lichess_tour_id, lichess_round_id, is_live, auto_refresh, last_fetched, game_count "
        "FROM pgn_sources WHERE id=?", (sid,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    return dict(zip(['id','organizer_id','source_type','source_url','tournament_name','round_num',
                     'round_name','lichess_tour_id','lichess_round_id','is_live','auto_refresh',
                     'last_fetched','game_count'], row))

def upsert_pgn_source(source_type: str, source_url: str, tournament_name: str,
                       round_num: int, round_name: str, lichess_tour_id: str,
                       lichess_round_id: str, is_live: bool, organizer_id: int = None,
                       auto_discovered: bool = False) -> int:
    conn = sqlite3.connect(DB_FILE)
    existing = None
    if lichess_round_id:
        existing = conn.execute(
            "SELECT id FROM pgn_sources WHERE lichess_round_id=?", (lichess_round_id,)
        ).fetchone()
    if not existing:
        existing = conn.execute(
            "SELECT id FROM pgn_sources WHERE source_url=? AND round_num=?",
            (source_url, round_num or 0)
        ).fetchone()
    if existing:
        conn.execute(
            "UPDATE pgn_sources SET tournament_name=?, is_live=?, lichess_tour_id=?, "
            "lichess_round_id=?, round_name=? WHERE id=?",
            (tournament_name, int(is_live), lichess_tour_id or None,
             lichess_round_id or None, round_name, existing[0])
        )
        conn.commit()
        conn.close()
        return existing[0]
    cur = conn.execute(
        "INSERT INTO pgn_sources (organizer_id, source_type, source_url, tournament_name, "
        "round_num, round_name, lichess_tour_id, lichess_round_id, is_live, auto_discovered) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (organizer_id, source_type, source_url, tournament_name,
         round_num or 0, round_name, lichess_tour_id or None,
         lichess_round_id or None, int(is_live), int(auto_discovered))
    )
    sid = cur.lastrowid
    conn.commit()
    conn.close()
    return sid

def update_pgn_source_fetched(sid: int, game_count: int):
    conn = sqlite3.connect(DB_FILE)
    conn.execute(
        "UPDATE pgn_sources SET last_fetched=datetime('now'), game_count=? WHERE id=?",
        (game_count, sid)
    )
    conn.commit()
    conn.close()

def list_live_pgn_sources() -> List[Dict]:
    conn = sqlite3.connect(DB_FILE)
    rows = conn.execute(
        "SELECT id, source_type, source_url, lichess_round_id, tournament_name, round_num "
        "FROM pgn_sources WHERE is_live=1 AND auto_refresh=1"
    ).fetchall()
    conn.close()
    return [dict(zip(['id','source_type','source_url','lichess_round_id','tournament_name','round_num'], r))
            for r in rows]

def delete_pgn_source(sid: int):
    conn = sqlite3.connect(DB_FILE)
    conn.execute("DELETE FROM pgn_games WHERE source_id=?", (sid,))
    conn.execute("DELETE FROM pgn_sources WHERE id=?", (sid,))
    conn.commit()
    conn.close()

# -- Games --

def _match_research_player(conn, name: str) -> Optional[int]:
    """Fuzzy match a PGN player name against player_research."""
    if not name:
        return None
    # Try exact match first
    row = conn.execute(
        "SELECT id FROM player_research WHERE name=? COLLATE NOCASE LIMIT 1", (name,)
    ).fetchone()
    if row:
        return row[0]
    # Try last name match (PGN: "Last, First" or "First Last")
    parts = [p.strip() for p in name.split(',')]
    last = parts[0]
    if len(last) >= 3:
        row = conn.execute(
            "SELECT id FROM player_research WHERE name LIKE ? COLLATE NOCASE LIMIT 1",
            (f"{last}%",)
        ).fetchone()
        if row:
            return row[0]
    return None

def insert_pgn_games(games: list) -> int:
    """Insert games (list of dicts from pgn_to_game_dict), skip duplicates. Returns count inserted."""
    if not games:
        return 0
    conn = sqlite3.connect(DB_FILE)
    inserted = 0
    for g in games:
        try:
            rw = _match_research_player(conn, g.get('white', ''))
            rb = _match_research_player(conn, g.get('black', ''))
            conn.execute(
                "INSERT OR IGNORE INTO pgn_games "
                "(source_id, event, site, date, round, white, black, result, "
                "white_elo, black_elo, eco, opening, time_control, raw_pgn, game_hash, "
                "research_white_id, research_black_id) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (g['source_id'], g.get('event',''), g.get('site',''), g.get('date',''),
                 g.get('round',''), g.get('white',''), g.get('black',''), g.get('result',''),
                 g.get('white_elo'), g.get('black_elo'), g.get('eco',''), g.get('opening',''),
                 g.get('time_control',''), g['raw_pgn'], g['game_hash'], rw, rb)
            )
            if conn.execute("SELECT changes()").fetchone()[0]:
                inserted += 1
        except Exception:
            pass
    conn.commit()
    conn.close()
    return inserted

def count_pgn_games_for_source(sid: int) -> int:
    conn = sqlite3.connect(DB_FILE)
    n = conn.execute("SELECT COUNT(*) FROM pgn_games WHERE source_id=?", (sid,)).fetchone()[0]
    conn.close()
    return n

def list_pgn_games(search: str = '', event: str = '', research_only: bool = False,
                   limit: int = 200, offset: int = 0) -> List[Dict]:
    conn = sqlite3.connect(DB_FILE)
    wheres, params = [], []
    if search:
        wheres.append("(white LIKE ? OR black LIKE ?)")
        params += [f'%{search}%', f'%{search}%']
    if event:
        wheres.append("event LIKE ?")
        params.append(f'%{event}%')
    if research_only:
        wheres.append("(research_white_id IS NOT NULL OR research_black_id IS NOT NULL)")
    where_sql = ("WHERE " + " AND ".join(wheres)) if wheres else ""
    rows = conn.execute(
        f"SELECT g.id, g.event, g.site, g.date, g.round, g.white, g.black, g.result, "
        f"g.white_elo, g.black_elo, g.eco, g.opening, g.research_white_id, g.research_black_id, "
        f"s.tournament_name, s.source_type "
        f"FROM pgn_games g LEFT JOIN pgn_sources s ON s.id=g.source_id "
        f"{where_sql} ORDER BY g.date DESC, g.id DESC LIMIT ? OFFSET ?",
        params + [limit, offset]
    ).fetchall()
    conn.close()
    cols = ['id','event','site','date','round','white','black','result','white_elo','black_elo',
            'eco','opening','research_white_id','research_black_id','tournament_name','source_type']
    return [dict(zip(cols, r)) for r in rows]

def count_pgn_games(search: str = '', event: str = '', research_only: bool = False) -> int:
    conn = sqlite3.connect(DB_FILE)
    wheres, params = [], []
    if search:
        wheres.append("(white LIKE ? OR black LIKE ?)")
        params += [f'%{search}%', f'%{search}%']
    if event:
        wheres.append("event LIKE ?")
        params.append(f'%{event}%')
    if research_only:
        wheres.append("(research_white_id IS NOT NULL OR research_black_id IS NOT NULL)")
    where_sql = ("WHERE " + " AND ".join(wheres)) if wheres else ""
    n = conn.execute(f"SELECT COUNT(*) FROM pgn_games {where_sql}", params).fetchone()[0]
    conn.close()
    return n

def get_pgn_game_raw(gid: int) -> Optional[str]:
    conn = sqlite3.connect(DB_FILE)
    row = conn.execute("SELECT raw_pgn FROM pgn_games WHERE id=?", (gid,)).fetchone()
    conn.close()
    return row[0] if row else None

def download_pgn_games(search: str = '', event: str = '',
                        research_only: bool = False) -> str:
    """Return all matching games concatenated as a single PGN string."""
    conn = sqlite3.connect(DB_FILE)
    wheres, params = [], []
    if search:
        wheres.append("(white LIKE ? OR black LIKE ?)")
        params += [f'%{search}%', f'%{search}%']
    if event:
        wheres.append("event LIKE ?")
        params.append(f'%{event}%')
    if research_only:
        wheres.append("(research_white_id IS NOT NULL OR research_black_id IS NOT NULL)")
    where_sql = ("WHERE " + " AND ".join(wheres)) if wheres else ""
    rows = conn.execute(
        f"SELECT raw_pgn FROM pgn_games {where_sql} ORDER BY date DESC, id DESC",
        params
    ).fetchall()
    conn.close()
    return '\n\n'.join(r[0] for r in rows)

def list_pgn_events() -> List[str]:
    conn = sqlite3.connect(DB_FILE)
    rows = conn.execute(
        "SELECT DISTINCT event FROM pgn_games WHERE event != '' ORDER BY event"
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]

def lichess_round_already_known(lichess_round_id: str) -> bool:
    conn = sqlite3.connect(DB_FILE)
    row = conn.execute(
        "SELECT id FROM pgn_sources WHERE lichess_round_id=?", (lichess_round_id,)
    ).fetchone()
    conn.close()
    return row is not None


def ensure_admin_exists():
    """Create a default admin/admin account if no users exist yet."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM users")
    count = c.fetchone()[0]
    conn.close()
    if count == 0:
        create_user("admin", "admin", "admin")

ensure_admin_exists()


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def get_setting(key: str, default: str = "") -> str:
    conn = sqlite3.connect(DB_FILE)
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row[0] if row else default

def set_setting(key: str, value: str):
    conn = sqlite3.connect(DB_FILE)
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()


def import_uscf_members(raw_bytes: bytes) -> int:
    """Stream-parse and bulk-insert TSV bytes from the USCF allratings download."""
    import io
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # Speed-optimised pragmas for bulk load
    c.execute("PRAGMA synchronous = OFF")
    c.execute("PRAGMA journal_mode = WAL")
    c.execute("PRAGMA cache_size = -64000")  # 64 MB cache
    c.execute("DELETE FROM uscf_members")
    SQL = "INSERT OR REPLACE INTO uscf_members (uscf_id, name, rating, state, expiry, fide_id) VALUES (?,?,?,?,?,?)"
    batch = []
    count = 0
    first_row_written = False
    reader = io.TextIOWrapper(io.BytesIO(raw_bytes), encoding="utf-8", errors="replace")
    for line in reader:
        parts = line.split('\t')
        if len(parts) < 5:
            continue
        uscf_id = parts[0].strip()
        if not uscf_id.isdigit():
            continue
        if not first_row_written or uscf_id == "31625896":
            import json as _json
            debug_path = DB_FILE.replace(".db", "_col_debug.json")
            label = uscf_id if uscf_id == "31625896" else "first_row"
            try:
                with open(debug_path) as _f:
                    existing = _json.load(_f)
            except Exception:
                existing = {}
            existing[label] = {str(i): v.strip() for i, v in enumerate(parts[:20])}
            with open(debug_path, "w") as _f:
                _json.dump(existing, _f, indent=2)
            first_row_written = True
        name   = parts[1].strip()
        state  = parts[2].strip() if len(parts) > 2 else ""
        expiry = parts[4].strip() if len(parts) > 4 else ""
        # col 6 = FIDE ID (7-9 digits), col 9 = regular rating (e.g. "1570*")
        import re as _re
        fide_raw = parts[6].strip() if len(parts) > 6 else ""
        fide_id  = fide_raw if _re.match(r'^\d{7,9}$', fide_raw) else None
        raw_r  = parts[9].strip() if len(parts) > 9 else ""
        m      = _re.search(r'(\d+)', raw_r)
        rating = int(m.group(1)) if m else 0
        batch.append((uscf_id, name, rating, state, expiry, fide_id))
        if len(batch) >= 50000:
            c.executemany(SQL, batch)
            conn.commit()
            count += len(batch)
            batch = []
    if batch:
        c.executemany(SQL, batch)
        conn.commit()
        count += len(batch)
    conn.close()
    return count


def search_uscf_members(q: str, limit: int = 12) -> List[Dict]:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    words = q.upper().split()
    if not words:
        conn.close()
        return []
    # Each word must match as a prefix of either the last name or the first name.
    # Names are stored as "LASTNAME, FIRSTNAME", so:
    #   last-name prefix  → name LIKE 'WORD%'
    #   first-name prefix → name LIKE '%, WORD%'
    conditions = " AND ".join(["(name LIKE ? OR name LIKE ?)" for _ in words])
    params = []
    for w in words:
        params.append(f"{w}%")      # last-name prefix
        params.append(f"%, {w}%")   # first-name prefix
    params.append(limit)
    c.execute(
        f"SELECT uscf_id, name, rating, state, fide_id FROM uscf_members WHERE {conditions} ORDER BY rating DESC LIMIT ?",
        params
    )
    rows = c.fetchall()
    conn.close()
    return [{"uscf_id": r[0], "name": r[1], "rating": r[2], "state": r[3], "fide_id": r[4]} for r in rows]


def lookup_uscf_member(uscf_id: str) -> Optional[Dict]:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT uscf_id, name, rating, state, fide_id, expiry FROM uscf_members WHERE uscf_id=?", (uscf_id.strip(),))
    row = c.fetchone()
    conn.close()
    if row:
        return {"uscf_id": row[0], "name": row[1], "rating": row[2], "state": row[3], "fide_id": row[4], "expiry": row[5]}
    return None


def get_uscf_db_count() -> int:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM uscf_members")
    count = c.fetchone()[0]
    conn.close()
    return count

def create_tournament(name: str, rounds: int = 5, system: str = "dutch", entry_fee: float = 0) -> int:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT INTO tournaments (name, rounds, system, entry_fee) VALUES (?, ?, ?, ?)", (name, rounds, system, entry_fee))
    tid = c.lastrowid
    conn.commit()
    conn.close()
    return tid


def get_tournaments() -> List[Dict]:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        SELECT id, name, rounds, system, current_round 
        FROM tournaments 
        ORDER BY created_at DESC
    """)
    rows = c.fetchall()
    conn.close()
    
    tournaments = []
    for row in rows:
        tournaments.append({
            "id": int(row[0]),
            "name": str(row[1]),
            "rounds": int(row[2]),
            "system": str(row[3]),
            "current_round": int(row[4]) if row[4] is not None else 0
        })
    return tournaments

def get_tournament(tid: int) -> Optional[Dict]:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT * FROM tournaments WHERE id=?", (tid,))
    row = c.fetchone()
    conn.close()
    return dict(zip([col[0] for col in c.description], row)) if row else None

def _fetch_live_uscf_rating(uscf_id: str) -> int:
    """Fetch live post-event USCF regular rating from MUIR sections API."""
    try:
        import httpx
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; MyChessRating/1.0)",
            "Accept": "application/json",
            "Origin": "https://ratings.uschess.org",
        }
        r = httpx.get(
            f"https://ratings-api.uschess.org/api/v1/members/{uscf_id}/sections",
            timeout=10, follow_redirects=True, headers=headers
        )
        if r.status_code == 200:
            for section in r.json().get("items", []):
                for record in section.get("ratingRecords", []):
                    if record.get("ratingSource") == "R":
                        return record.get("postRating", 0)
    except Exception:
        pass
    return 0


def _fetch_thin3(uscf_id: str) -> dict:
    """Fetch fallback USCF rating from thin3.php (used only when player not in local DB)."""
    result = {"rating": 0}
    try:
        import httpx, re
        headers = {"User-Agent": "Mozilla/5.0 (compatible; MyChessRating/1.0)"}
        r = httpx.get(f"http://www.uschess.org/msa/thin3.php?{uscf_id.strip()}",
                      timeout=8, follow_redirects=True, headers=headers)
        if r.status_code == 200:
            m = re.search(r"name=rating1[^>]+value='([^']+)'", r.text)
            if m:
                num = re.search(r"(\d+)", m.group(1))
                if num: result["rating"] = int(num.group(1))
    except Exception:
        pass
    return result


def _fetch_fide_rating(fide_id: str) -> int:
    """Fetch official FIDE standard rating by scraping ratings.fide.com.
    Returns 0 for unrated players (no match on page)."""
    try:
        import httpx, re
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
        r = httpx.get(f"https://ratings.fide.com/profile/{fide_id}",
                      timeout=10, follow_redirects=True, headers=headers)
        if r.status_code == 200:
            m = re.search(r'class="profile-standart[^"]*"[^>]*>.*?<p>(\d+)</p>', r.text, re.DOTALL)
            if m:
                return int(m.group(1))
    except Exception:
        pass
    return 0


def add_player(tid: int, name: str, uscf_id: Optional[str] = None, rating: Optional[int] = None,
               email: Optional[str] = None, fide_id: Optional[str] = None, expiry: Optional[str] = None):
    fide_rating = 0
    live_rating = 0
    if uscf_id:
        # Always check local DB — fills rating, fide_id, expiry regardless of what form submitted
        local = lookup_uscf_member(uscf_id)
        if local:
            if not rating:
                rating = local.get("rating") or 0
            if not fide_id:
                fide_id = local.get("fide_id")
            if not expiry:
                expiry = local.get("expiry")
        # Fallback USCF rating via thin3.php if not in local DB
        if not rating:
            thin3 = _fetch_thin3(uscf_id)
            rating = thin3.get("rating", 0)
        live_rating = _fetch_live_uscf_rating(uscf_id)
    fide_rating = _fetch_fide_rating(fide_id) if fide_id else 0
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT INTO players (tournament_id, name, uscf_id, rating, email, fide_id, expiry, fide_rating, live_rating) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
              (tid, name.strip(), uscf_id, rating or 0, email, fide_id, expiry, fide_rating, live_rating))
    conn.commit()
    conn.close()

def get_players(tid: int) -> List[Dict]:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT * FROM players WHERE tournament_id=? ORDER BY rating DESC, name", (tid,))
    rows = c.fetchall()
    cols = [col[0] for col in c.description]
    conn.close()
    return [dict(zip(cols, row)) for row in rows]

def delete_player(pid: int) -> Optional[int]:
    """Delete player and return their tournament_id."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT tournament_id FROM players WHERE id=?", (pid,))
    row = c.fetchone()
    tid = row[0] if row else None
    c.execute("DELETE FROM players WHERE id=?", (pid,))
    conn.commit()
    conn.close()
    return tid

def record_result(tid: int, round_num: int, white_id: Optional[int] = None, black_id: Optional[int] = None,
                  result: Optional[str] = None, is_bye: bool = False, bye_type: str = "none"):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    pts_w = pts_b = 0.0
    res_code = result or "-"
    
    if is_bye:
        if bye_type == "full":
            pts_w = 1.0
            res_code = "F"  # full bye
        elif bye_type == "half":
            pts_w = 0.5
            res_code = "H"
        else:
            pts_w = 0.0
            res_code = "Z"
    elif result:
        if result in ("1-0", "1F-0F"):
            pts_w, pts_b = 1.0, 0.0
        elif result in ("0-1", "0F-1F"):
            pts_w, pts_b = 0.0, 1.0
        elif result == "1/2-1/2":
            pts_w, pts_b = 0.5, 0.5
    
    if white_id and black_id and not is_bye:
        c.execute("""SELECT id FROM results
                     WHERE tournament_id=? AND round=? AND white_id=? AND black_id=?""",
                  (tid, round_num, white_id, black_id))
        existing = c.fetchone()
        if existing:
            c.execute("UPDATE results SET result=? WHERE id=?", (result or res_code, existing[0]))
        else:
            c.execute("""INSERT INTO results
                         (tournament_id, round, white_id, black_id, result)
                         VALUES (?, ?, ?, ?, ?)""",
                      (tid, round_num, white_id, black_id, result or res_code))
    
    def update_running(pid: int, pts: float):
        c.execute("""SELECT score_after FROM player_round_scores 
                     WHERE tournament_id=? AND player_id=? AND round=?""",
                  (tid, pid, round_num - 1))
        prev = c.fetchone()
        prev_score = prev[0] if prev else 0.0
        c.execute("""INSERT OR REPLACE INTO player_round_scores 
                     (tournament_id, player_id, round, score_after) 
                     VALUES (?, ?, ?, ?)""", 
                  (tid, pid, round_num, prev_score + pts))
    
    if white_id:
        update_running(white_id, pts_w)
    if black_id:
        update_running(black_id, pts_b)
    
    conn.commit()
    conn.close()
    recalculate_scores(tid)

def recalculate_scores(tid: int):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE players SET score = 0 WHERE tournament_id=?", (tid,))
    c.execute("SELECT white_id, black_id, result FROM results WHERE tournament_id=?", (tid,))
    for w, b, res in c.fetchall():
        if res in ("1-0", "1F-0F"):
            c.execute("UPDATE players SET score = score + 1 WHERE id=?", (w,))
        elif res in ("0-1", "0F-1F"):
            c.execute("UPDATE players SET score = score + 1 WHERE id=?", (b,))
        elif res == "1/2-1/2":
            c.execute("UPDATE players SET score = score + 0.5 WHERE id IN (?, ?)", (w, b))
    conn.commit()
    conn.close()

def get_pairings_for_round(tid: int, round_num: int) -> List[Dict]:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""SELECT r.id as result_id, r.white_id, r.black_id,
                        pw.name as white_name, pb.name as black_name, r.result,
                        pw.rating as white_rating, pb.rating as black_rating,
                        pw.fide_rating as white_fide_rating, pb.fide_rating as black_fide_rating
                 FROM results r
                 LEFT JOIN players pw ON r.white_id = pw.id
                 LEFT JOIN players pb ON r.black_id = pb.id
                 WHERE r.tournament_id=? AND r.round=?
                 ORDER BY r.id""", (tid, round_num))
    rows = c.fetchall()
    conn.close()
    cols = [col[0] for col in c.description]
    return [dict(zip(cols, row)) for row in rows]

def get_standings(tid: int) -> List[Dict]:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, name, rating, score FROM players WHERE tournament_id=? ORDER BY score DESC, rating DESC", (tid,))
    players = [dict(zip([col[0] for col in c.description], row)) for row in c.fetchall()]
    conn.close()
    
    for p in players:
        pid = p["id"]
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT score_after FROM player_round_scores WHERE tournament_id=? AND player_id=? ORDER BY round", (tid, pid))
        running = [r[0] for r in c.fetchall()]
        conn.close()
        p["progressive"] = round(sum(running), 1)
        p["cumulative"] = p["progressive"]
        p["buchholz"] = round(p["score"] * 2, 1)  # Replace with full opponent sum in production
        p["sonneborn_berger"] = round(p["score"] * 1.5, 1)
        p["median"] = round(p["score"], 1)
        p["solkoff"] = p["buchholz"]
    
    players.sort(key=lambda x: (-x["score"], -x.get("buchholz", 0), -x.get("sonneborn_berger", 0),
                                -x.get("median", 0), -x.get("progressive", 0)))
    return players

def update_current_round(tid: int, new_round: int):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE tournaments SET current_round=? WHERE id=?", (new_round, tid))
    conn.commit()
    conn.close()

def store_pairing(tid: int, round_num: int, white_id: int, black_id: Optional[int]):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""INSERT INTO results (tournament_id, round, white_id, black_id, result)
                 VALUES (?, ?, ?, ?, '*')""",
              (tid, round_num, white_id, black_id))
    conn.commit()
    conn.close()

def get_player_rank_map(tid: int) -> Dict[int, int]:
    """Returns {trf_rank: player_id} using the same ordering as build_trf."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id FROM players WHERE tournament_id=? ORDER BY rating DESC, registered_at", (tid,))
    rows = c.fetchall()
    conn.close()
    return {rank: row[0] for rank, row in enumerate(rows, 1)}


def update_tournament_settings(tid: int, entry_fee: float, registration_open: int):
    conn = sqlite3.connect(DB_FILE)
    conn.execute(
        "UPDATE tournaments SET entry_fee=?, registration_open=? WHERE id=?",
        (entry_fee, registration_open, tid)
    )
    conn.commit()
    conn.close()


def get_player(pid: int) -> Optional[Dict]:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT * FROM players WHERE id=?", (pid,))
    row = c.fetchone()
    cols = [col[0] for col in c.description]
    conn.close()
    return dict(zip(cols, row)) if row else None


def register_player_public(tid: int, name: str, uscf_id: Optional[str], rating: int,
                            email: Optional[str], phone: Optional[str], fide_id: Optional[str],
                            expiry: Optional[str], requested_byes: list, payment_status: str) -> int:
    import json
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        """INSERT INTO players
           (tournament_id, name, uscf_id, rating, email, phone, fide_id, expiry,
            status, payment_status, requested_byes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)""",
        (tid, name, uscf_id, rating or 0, email, phone, fide_id, expiry,
         payment_status, json.dumps(requested_byes or []))
    )
    pid = c.lastrowid
    conn.commit()
    conn.close()
    return pid


def set_player_status(pid: int, status: str) -> Optional[int]:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT tournament_id FROM players WHERE id=?", (pid,))
    row = c.fetchone()
    tid = row[0] if row else None
    c.execute("UPDATE players SET status=? WHERE id=?", (status, pid))
    conn.commit()
    conn.close()
    return tid


def update_player_payment(pid: int, payment_status: str, payment_intent_id: Optional[str] = None):
    conn = sqlite3.connect(DB_FILE)
    if payment_intent_id:
        conn.execute(
            "UPDATE players SET payment_status=?, payment_intent_id=? WHERE id=?",
            (payment_status, payment_intent_id, pid)
        )
    else:
        conn.execute("UPDATE players SET payment_status=? WHERE id=?", (payment_status, pid))
    conn.commit()
    conn.close()


def update_player_bye_request(pid: int, round_num: int, action: str) -> Optional[int]:
    import json
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT tournament_id, requested_byes FROM players WHERE id=?", (pid,))
    row = c.fetchone()
    if not row:
        conn.close()
        return None
    tid = row[0]
    try:
        byes = json.loads(row[1] or '[]')
    except Exception:
        byes = []
    if action == "add" and round_num not in byes:
        byes.append(round_num)
        byes.sort()
    elif action == "remove" and round_num in byes:
        byes.remove(round_num)
    c.execute("UPDATE players SET requested_byes=? WHERE id=?", (json.dumps(byes), pid))
    conn.commit()
    conn.close()
    return tid


# ---------------------------------------------------------------------------
# Chess Federations & Cities
# ---------------------------------------------------------------------------

def _fed_row(row, cols) -> Dict:
    return dict(zip(cols, row))

def list_federations(active_only: bool = False) -> List[Dict]:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    sql = "SELECT * FROM chess_federations"
    if active_only:
        sql += " WHERE active=1"
    sql += " ORDER BY display_order, name"
    c.execute(sql)
    cols = [d[0] for d in c.description]
    rows = [_fed_row(r, cols) for r in c.fetchall()]
    conn.close()
    return rows

def list_countries() -> List[Dict]:
    """Return unique countries that have at least one active federation."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        SELECT country_code, country_name, MIN(display_order) as ord
        FROM chess_federations WHERE active=1
        GROUP BY country_code, country_name
        ORDER BY country_name
    """)
    rows = [{"country_code": r[0], "country_name": r[1]} for r in c.fetchall()]
    conn.close()
    return rows

def get_federations_for_country(country_code: str) -> List[Dict]:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT * FROM chess_federations WHERE country_code=? AND active=1 ORDER BY display_order, name",
              (country_code,))
    cols = [d[0] for d in c.description]
    rows = [_fed_row(r, cols) for r in c.fetchall()]
    conn.close()
    return rows

def get_federation(fid: int) -> Optional[Dict]:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT * FROM chess_federations WHERE id=?", (fid,))
    row = c.fetchone()
    cols = [d[0] for d in c.description]
    conn.close()
    return _fed_row(row, cols) if row else None

def create_federation(name: str, abbreviation: str, country_code: str, country_name: str,
                      rating_system: str, website_url: str, tournaments_url: str,
                      display_order: int = 0) -> int:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""INSERT INTO chess_federations
                 (name, abbreviation, country_code, country_name, rating_system,
                  website_url, tournaments_url, active, display_order)
                 VALUES (?,?,?,?,?,?,?,1,?)""",
              (name, abbreviation, country_code, country_name, rating_system,
               website_url, tournaments_url, display_order))
    fid = c.lastrowid
    conn.commit()
    conn.close()
    return fid

def update_federation(fid: int, **kwargs):
    allowed = {"name", "abbreviation", "country_code", "country_name", "rating_system",
                "website_url", "tournaments_url", "active", "display_order"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    conn = sqlite3.connect(DB_FILE)
    sets = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE chess_federations SET {sets} WHERE id=?", (*fields.values(), fid))
    conn.commit()
    conn.close()

def delete_federation(fid: int):
    conn = sqlite3.connect(DB_FILE)
    conn.execute("DELETE FROM federation_cities WHERE federation_id=?", (fid,))
    conn.execute("DELETE FROM chess_federations WHERE id=?", (fid,))
    conn.commit()
    conn.close()

def list_cities(federation_id: int) -> List[Dict]:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT * FROM federation_cities WHERE federation_id=? AND active=1 ORDER BY display_order, city_name",
              (federation_id,))
    cols = [d[0] for d in c.description]
    rows = [dict(zip(cols, r)) for r in c.fetchall()]
    conn.close()
    return rows

def create_city(federation_id: int, city_name: str, region: str = '', display_order: int = 0) -> int:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT INTO federation_cities (federation_id, city_name, region, display_order) VALUES (?,?,?,?)",
              (federation_id, city_name.strip(), region.strip(), display_order))
    cid = c.lastrowid
    conn.commit()
    conn.close()
    return cid

def delete_city(cid: int):
    conn = sqlite3.connect(DB_FILE)
    conn.execute("DELETE FROM federation_cities WHERE id=?", (cid,))
    conn.commit()
    conn.close()

def update_user_location(user_id: int, country_code: str, city: str, federation_id: Optional[int]):
    conn = sqlite3.connect(DB_FILE)
    conn.execute("UPDATE users SET country_code=?, city=?, federation_id=? WHERE id=?",
                 (country_code, city, federation_id, user_id))
    conn.commit()
    conn.close()
