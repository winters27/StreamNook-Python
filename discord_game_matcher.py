"""
Discord Game Image Matcher
===============================================

"""

import re
from difflib import SequenceMatcher
from typing import Optional, Dict, Any, List


def _norm(s: str) -> str:
    """Normalize string for comparison."""
    return (s or "").strip().casefold()


def _clean_game_name(name: str) -> str:
    """
    Clean game name by removing common suffixes/prefixes that may cause mismatches.
    """
    if not name:
        return ""
    
    # Remove trademark symbols
    name = re.sub(r'[®™©]', '', name)
    
    # Remove common subtitle patterns
    patterns_to_remove = [
        r'\s*\(.*?\)\s*',
        r'\s*\[.*?\]\s*',
        r'\s*:\s*[Tt]he\s+',
        r'\s*-\s*[Ss]eason\s+\d+',
        r'\s*[Ee]dition$',
        r'\s*[Rr]emastered$',
        r'\s*[Dd]efinitive\s+[Ee]dition$',
    ]
    
    cleaned = name
    for pattern in patterns_to_remove:
        cleaned = re.sub(pattern, ' ', cleaned)
    
    cleaned = ' '.join(cleaned.split())
    return _norm(cleaned)


def _similarity_score(s1: str, s2: str) -> float:
    """Calculate similarity score between two strings."""
    return SequenceMatcher(None, s1, s2).ratio()


def _extract_core_name(name: str) -> str:
    """Extract the core game name (first part before colon or dash)."""
    for sep in [':', ' - ', '–', '—']:
        if sep in name:
            return name.split(sep)[0].strip()
    return name


def _tokenize(name: str) -> set:
    """Create a set of tokens from a game name for token-based matching."""
    stop_words = {'the', 'a', 'an', 'of', 'in', 'on', 'at', 'to', 'for'}
    tokens = set(_norm(name).split())
    return tokens - stop_words


def resolve_discord_game_image_improved(
    game_name: str,
    discord_apps: List[Dict[str, Any]],
    threshold: float = 0.6,
    size: int = 512,
    debug: bool = False
) -> Optional[str]:
    """
    Improved version of resolve_discord_game_image with better matching.
    CORRECTED: Uses icon_hash field instead of icon field.
    """
    if not game_name or not discord_apps:
        return None
    
    g_norm = _norm(game_name)
    g_clean = _clean_game_name(game_name)
    g_core = _norm(_extract_core_name(game_name))
    g_tokens = _tokenize(game_name)
    
    best_match = None
    best_score = 0.0
    
    for app in discord_apps:
        app_name = app.get("name", "")
        if not app_name:
            continue
        
        names_to_check = [app_name]
        aliases = app.get("aliases") or []
        names_to_check.extend(aliases)
        
        for check_name in names_to_check:
            score = 0.0
            
            cn_norm = _norm(check_name)
            cn_clean = _clean_game_name(check_name)
            cn_core = _norm(_extract_core_name(check_name))
            cn_tokens = _tokenize(check_name)
            
            if g_norm == cn_norm:
                score = 1.0
            elif g_clean == cn_clean:
                score = 0.95
            elif g_core and cn_core and g_core == cn_core:
                score = 0.9
            elif g_norm in cn_norm or cn_norm in g_norm:
                overlap_len = min(len(g_norm), len(cn_norm))
                max_len = max(len(g_norm), len(cn_norm))
                score = 0.8 + (0.1 * overlap_len / max_len)
            else:
                sim_norm = _similarity_score(g_norm, cn_norm)
                sim_clean = _similarity_score(g_clean, cn_clean)
                sim_core = _similarity_score(g_core, cn_core) if g_core and cn_core else 0
                
                score = max(sim_norm, sim_clean, sim_core)
                
                if g_tokens and cn_tokens:
                    common_tokens = g_tokens & cn_tokens
                    if common_tokens:
                        token_score = len(common_tokens) / max(len(g_tokens), len(cn_tokens))
                        score = max(score, token_score * 0.85)
            
            if score > best_score and score >= threshold:
                best_score = score
                best_match = app
    
    if not best_match:
        if debug:
            print(f"[Discord Match] '{game_name}' -> No match found (threshold: {threshold})")
        return None
    
    # Get the app data
    app_id = best_match.get("id")
    
    # CORRECTED: Check for icon_hash (not icon) and cover_image
    icon_hash = best_match.get("icon_hash")  # <-- CHANGED FROM "icon" to "icon_hash"
    cover_image = best_match.get("cover_image")
    
    if debug:
        print(f"[Discord Match] '{game_name}' -> '{best_match.get('name')}' (score: {best_score:.3f})")
        print(f"  App ID: {app_id}")
        print(f"  icon_hash: {icon_hash}")
        print(f"  cover_image: {cover_image}")
    
    # Prefer cover image, fall back to icon_hash
    if cover_image and app_id:
        url = f"https://cdn.discordapp.com/app-assets/{app_id}/{cover_image}.png?size={size}"
        if debug:
            print(f"  → Using COVER: {url}")
        return url
    
    if icon_hash and app_id:
        url = f"https://cdn.discordapp.com/app-icons/{app_id}/{icon_hash}.png?size={size}"
        if debug:
            print(f"  → Using ICON: {url}")
        return url
    
    if debug:
        print(f"  → No image available")
    
    return None


# Drop-in replacement that matches your original signature
def resolve_discord_game_image(game_name: str, debug: bool = False) -> Optional[str]:
    """
    Drop-in replacement for the original function.
    Fetches Discord apps and uses improved matching.
    """
    import json
    import urllib.request
    import time
    from pathlib import Path
    
    # Use same cache location as original
    APP_DIR = Path(__file__).parent / "data"
    _DETECTABLE_CACHE = APP_DIR / "discord_detectables_cache.json"
    
    def _load_detectable_cache() -> dict:
        try:
            if _DETECTABLE_CACHE.exists():
                return json.loads(_DETECTABLE_CACHE.read_text("utf-8"))
        except Exception:
            pass
        return {"fetched_at": 0, "apps": []}
    
    def _save_detectable_cache(cache: dict):
        try:
            APP_DIR.mkdir(parents=True, exist_ok=True)
            _DETECTABLE_CACHE.write_text(json.dumps(cache), encoding="utf-8")
        except Exception:
            pass
    
    def _fetch_detectables_from_discord(timeout=6) -> list:
        cache = _load_detectable_cache()
        if time.time() - cache.get("fetched_at", 0) < 12 * 3600 and cache.get("apps"):
            return cache["apps"]
        
        try:
            req = urllib.request.Request(
                "https://discord.com/api/v9/applications/detectable",
                headers={"User-Agent": "StreamNook/1.0", "Accept": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=timeout) as r:
                apps = json.loads(r.read().decode("utf-8", "ignore")) or []
            cache = {"fetched_at": int(time.time()), "apps": apps}
            _save_detectable_cache(cache)
            return apps
        except Exception:
            return cache.get("apps", [])
    
    # Fetch apps and use improved matching
    apps = _fetch_detectables_from_discord()
    return resolve_discord_game_image_improved(game_name, apps, threshold=0.6, debug=debug)