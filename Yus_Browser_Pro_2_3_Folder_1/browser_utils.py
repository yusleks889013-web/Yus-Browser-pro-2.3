from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import quote_plus

# Browser search templates are normal web search pages; no API key is needed.
# The selected engine can always be changed from the toolbar or Settings.
SEARCH_ENGINES: dict[str, str] = {
    "Google": "https://www.google.com/search?q={}",
    "Bing": "https://www.bing.com/search?q={}",
    "DuckDuckGo": "https://duckduckgo.com/?q={}",
    "Brave Search": "https://search.brave.com/search?q={}",
    "Startpage": "https://www.startpage.com/sp/search?query={}",
    "Ecosia": "https://www.ecosia.org/search?q={}",
    "Yahoo": "https://search.yahoo.com/search?p={}",
    "Yandex": "https://yandex.kz/search/?text={}",
}

DEFAULT_SEARCH_ENGINE = "Yandex"
DEFAULT_SEARCH_URL = SEARCH_ENGINES[DEFAULT_SEARCH_ENGINE]
SEARCH_HOME_URLS: dict[str, str] = {
    "Google": "https://www.google.com/",
    "Bing": "https://www.bing.com/",
    "DuckDuckGo": "https://duckduckgo.com/",
    "Brave Search": "https://search.brave.com/",
    "Startpage": "https://www.startpage.com/",
    "Ecosia": "https://www.ecosia.org/",
    "Yahoo": "https://search.yahoo.com/",
    "Yandex": "https://yandex.kz/",
}

# Search shortcuts work regardless of the currently selected default engine.
# Example: yt: cats, img: purple wallpaper, wiki: WireGuard, !g python.
SPECIAL_SEARCHES: dict[str, str] = {
    "yt": "https://www.youtube.com/results?search_query={}",
    "youtube": "https://www.youtube.com/results?search_query={}",
    "video": "https://www.youtube.com/results?search_query={}",
    "видео": "https://www.youtube.com/results?search_query={}",
    "img": "https://www.google.com/search?tbm=isch&q={}",
    "images": "https://www.google.com/search?tbm=isch&q={}",
    "картинки": "https://www.google.com/search?tbm=isch&q={}",
    "news": "https://news.google.com/search?q={}",
    "новости": "https://news.google.com/search?q={}",
    "maps": "https://www.google.com/maps/search/?api=1&query={}",
    "map": "https://www.google.com/maps/search/?api=1&query={}",
    "карта": "https://www.google.com/maps/search/?api=1&query={}",
    "wiki": "https://ru.wikipedia.org/w/index.php?search={}",
    "вики": "https://ru.wikipedia.org/w/index.php?search={}",
    "g": SEARCH_ENGINES["Google"],
    "google": SEARCH_ENGINES["Google"],
    "b": SEARCH_ENGINES["Bing"],
    "bing": SEARCH_ENGINES["Bing"],
    "ddg": SEARCH_ENGINES["DuckDuckGo"],
    "duckduckgo": SEARCH_ENGINES["DuckDuckGo"],
    "brave": SEARCH_ENGINES["Brave Search"],
    "startpage": SEARCH_ENGINES["Startpage"],
    "sp": SEARCH_ENGINES["Startpage"],
    "ecosia": SEARCH_ENGINES["Ecosia"],
    "eco": SEARCH_ENGINES["Ecosia"],
    "yahoo": SEARCH_ENGINES["Yahoo"],
    "yandex": SEARCH_ENGINES["Yandex"],
    "ya": SEARCH_ENGINES["Yandex"],
    "gi": "https://www.google.com/search?tbm=isch&q={}",
    "gn": "https://news.google.com/search?q={}",
    "gm": "https://www.google.com/maps/search/?api=1&query={}",
    "w": "https://ru.wikipedia.org/w/index.php?search={}",
    "tr": "https://translate.google.com/?sl=auto&tl=ru&text={}&op=translate",
    "translate": "https://translate.google.com/?sl=auto&tl=ru&text={}&op=translate",
    "gh": "https://github.com/search?q={}&type=repositories",
    "github": "https://github.com/search?q={}&type=repositories",
    "reddit": "https://www.reddit.com/search/?q={}",
    "music": "https://music.youtube.com/search?q={}",
    "shop": "https://www.google.com/search?tbm=shop&q={}",
    "pdf": "https://www.google.com/search?q=filetype%3Apdf+{}",
    "scholar": "https://scholar.google.com/scholar?q={}",
}

_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")
_DOMAIN_RE = re.compile(
    r"^(?:[\w\u0080-\uffff](?:[\w\u0080-\uffff-]{0,61}[\w\u0080-\uffff])?\.)+"
    r"[\w\u0080-\uffff-]{2,63}(?::\d{1,5})?(?:[/?#].*)?$"
)
_IPV4_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}(?::\d{1,5})?(?:[/?#].*)?$")
_IPV6_BRACKET_RE = re.compile(r"^\[[0-9a-fA-F:.%]+\](?::\d{1,5})?(?:[/?#].*)?$")
_ALIAS_RE = re.compile(r"^(?:!([a-zA-Z]+)|([a-zA-Zа-яА-ЯёЁ]+))\s*(?::|\s)\s*(.+)$", re.DOTALL)


def search_url_for(engine: str | None) -> str:
    """Return a known search template, falling back to Google for bad settings."""
    return SEARCH_ENGINES.get(str(engine or "").strip(), DEFAULT_SEARCH_URL)


def search_home_for(engine: str | None) -> str:
    return SEARCH_HOME_URLS.get(str(engine or "").strip(), SEARCH_HOME_URLS[DEFAULT_SEARCH_ENGINE])


def _search_url(query: str, template: str) -> str:
    return template.format(quote_plus(query.strip()))


def _is_windows_path(value: str) -> bool:
    return bool(re.match(r"^(?:[a-zA-Z]:[\\/]|\\\\[^\\]+\\[^\\]+)", value))


def resolve_address(text: str, search_url: str = DEFAULT_SEARCH_URL) -> str:
    """Resolve address-bar input to a local/web URL or a search URL.

    Features: safe trimming, direct URLs, local paths when they exist, multiple
    search operators (yt:, img:, news:, maps:, wiki:, !g, !yt and provider aliases),
    and sensible default search for plain phrases.
    """
    value = (text or "").replace("\u200b", "").replace("\ufeff", "").strip()
    if not value:
        return "https://yandex.kz/"

    # A user explicitly typing a supported scheme is navigating, not searching.
    # javascript: is intentionally not accepted from the address bar.
    if re.match(r"^(?:about|file|data|mailto|ftp|http|https):", value, re.IGNORECASE):
        if value.lower().startswith("javascript:"):
            return _search_url(value, search_url)
        return value

    # Support an explicitly selected local path only when it exists.
    if _is_windows_path(value):
        try:
            path = Path(value).expanduser()
            if path.exists():
                return path.resolve().as_uri()
        except (OSError, ValueError):
            pass

    # Search aliases: query text after alias is safely URL encoded.
    match = _ALIAS_RE.match(value)
    if match:
        alias = (match.group(1) or match.group(2) or "").lower()
        query = match.group(3).strip()
        template = SPECIAL_SEARCHES.get(alias)
        if template and query:
            return _search_url(query, template)

    # A leading '?' explicitly asks for a web search using the chosen provider.
    if value.startswith("?"):
        query = value[1:].strip()
        return _search_url(query, search_url) if query else "https://yandex.kz/"

    # Preserve explicit URL schemes; common local development hosts use HTTP.
    if value.lower() == "localhost" or value.lower().startswith("localhost:") or value.lower().startswith("localhost/"):
        return "http://" + value
    if _IPV4_RE.match(value) or _IPV6_BRACKET_RE.match(value):
        return "http://" + value

    # A clear domain or domain with path goes directly to the site.
    if _DOMAIN_RE.match(value) and not value.startswith(".") and ".." not in value:
        return "https://" + value

    # If it isn't an address, search the full text with correct URL encoding.
    return _search_url(value, search_url)
