"""Normalización de países a código ISO 3166-1 alfa-2.

La ORI escribe los países en inglés ("Belgium") y MoveON en castellano ("Bélgica"); sin esto la
misma universidad se duplicaría. Añadir aquí cualquier país nuevo que aparezca.
"""

import re
import unicodedata

_NOMBRES = {
    "DE": ["alemania", "germany", "deutschland"],
    "AR": ["argentina"],
    "AT": ["austria"],
    "BE": ["belgica", "belgium"],
    "BR": ["brasil", "brazil"],
    "BG": ["bulgaria"],
    "CL": ["chile"],
    "CN": ["china"],
    "CY": ["chipre", "cyprus"],
    "CO": ["colombia"],
    "KR": ["corea del sur", "south korea", "korea, republic of", "republic of korea"],
    "CR": ["costa rica"],
    "HR": ["croacia", "croatia"],
    "CZ": ["republica checa", "chequia", "czech republic", "czechia"],
    "DK": ["dinamarca", "denmark"],
    "EC": ["ecuador"],
    "SK": ["eslovaquia", "slovakia"],
    "SI": ["eslovenia", "slovenia"],
    "ES": ["espana", "spain"],
    "US": ["estados unidos", "united states", "usa"],
    "EE": ["estonia"],
    "FI": ["finlandia", "finland"],
    "FR": ["francia", "france"],
    "GE": ["georgia"],
    "GR": ["grecia", "greece"],
    "HU": ["hungria", "hungary"],
    "IE": ["irlanda", "ireland"],
    "IS": ["islandia", "iceland"],
    "IT": ["italia", "italy"],
    "JP": ["japon", "japan"],
    "LV": ["letonia", "latvia"],
    "LI": ["liechtenstein"],
    "LT": ["lituania", "lithuania"],
    "MK": ["macedonia del norte", "north macedonia", "macedonia"],
    "MX": ["mexico"],
    "NO": ["noruega", "norway"],
    "NL": ["paises bajos", "netherlands", "holanda", "the netherlands"],
    "PA": ["panama"],
    "PE": ["peru"],
    "PL": ["polonia", "poland"],
    "PT": ["portugal"],
    "GB": ["reino unido", "united kingdom", "uk"],
    "RO": ["rumania", "rumania", "romania"],
    "SE": ["suecia", "sweden"],
    "CH": ["suiza", "switzerland"],
    "TR": ["turquia", "turkey", "turkiye"],
    "UY": ["uruguay"],
}


def _norm(texto: str) -> str:
    s = unicodedata.normalize("NFKD", texto or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().lower()


_INDICE = {nombre: iso for iso, nombres in _NOMBRES.items() for nombre in nombres}


def codigo_pais(nombre: str) -> str:
    """Devuelve el código ISO alfa-2; si no lo reconoce, el nombre tal cual (para revisarlo)."""
    n = _norm(nombre)
    if len(n) == 2 and n.upper() in _NOMBRES:
        return n.upper()
    return _INDICE.get(n, (nombre or "").strip())
