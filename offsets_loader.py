"""
FearlessHack — Offsets Loader
Авто-обновление оффсетов с GitHub (a2x/cs2-dumper).
Fallback: хардкод значения если интернета нет.
"""

import json
import os
import time
import urllib.request
import urllib.error


# ==================== ХАРДКОД (fallback) ====================
FALLBACK_OFFSETS = {
    # client.dll global
    "dwEntityList":               0x2711598,
    "dwLocalPlayerPawn":          0x255C3C8,
    "dwLocalPlayerController":    0x25338A8,
    "dwViewMatrix":               0x2561CD0,
    "dwViewAngles":               0x2572AE8,

    # controller
    "m_hPawn":                    0x6BC,
    "m_bIsLocalPlayerController": 0x790,

    # entity base
    "m_pGameSceneNode":           0x330,
    "m_iHealth":                  0x34C,
    "m_iTeamNum":                 0x3E7,
    "m_lifeState":                0x354,
    "m_fFlags":                   0x3F4,

    # pawn
    "m_vOldOrigin":               0x14A4,
    "m_vecViewOffset":            0xF60,

    # scene node
    "m_bDormant":                 0x103,

    # weapon
    "m_pWeaponServices":          0x12F0,
    "m_hActiveWeapon":            0x60,
    "m_AttributeManager":         0x1290,
    "m_Item":                     0x50,
    "m_iItemDefinitionIndex":     0x1BA,

    # misc pawn
    "m_iIDEntIndex":              0x36DC,
    "m_entitySpottedState":       0x1E88,
    "m_bSpottedByMask":           0xC,
}


# ==================== КОНФИГ ====================
CACHE_FILE = "offsets_cache.json"
CACHE_MAX_AGE_H = 12  # кэш живёт 12 часов

# Зеркала (пробуем по очереди)
MIRRORS = [
    "https://raw.githubusercontent.com/a2x/cs2-dumper/main/output/offsets.json",
    "https://cdn.jsdelivr.net/gh/a2x/cs2-dumper@main/output/offsets.json",
]

# Какие имена брать из offsets.json и в какой модуль смотреть
OFFSET_MAP = {
    # name_in_our_code: (module, key_in_json)
    "dwEntityList":              ("client.dll", "dwEntityList"),
    "dwLocalPlayerPawn":         ("client.dll", "dwLocalPlayerPawn"),
    "dwLocalPlayerController":   ("client.dll", "dwLocalPlayerController"),
    "dwViewMatrix":              ("client.dll", "dwViewMatrix"),
    "dwViewAngles":              ("client.dll", "dwViewAngles"),
}


# ==================== ЛОГ ====================
def _log(msg):
    print(f"[OFFSETS] {msg}")


# ==================== СКАЧИВАНИЕ ====================
def _download_json(url, timeout=5):
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "FearlessHack/offsets-loader"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read().decode("utf-8")
            return json.loads(data)
    except (urllib.error.URLError, urllib.error.HTTPError,
            TimeoutError, json.JSONDecodeError) as e:
        _log(f"download failed ({url}): {e}")
        return None


def fetch_offsets():
    """Пробует зеркала по очереди. Возвращает dict или None."""
    for url in MIRRORS:
        _log(f"trying {url}")
        data = _download_json(url)
        if data and "client.dll" in data:
            _log(f"OK from {url}")
            return data
    _log("all mirrors failed")
    return None


# ==================== ПАРСИНГ ====================
def parse_offsets(raw_json):
    """Из сырого JSON дампера строит плоский dict {name: int}."""
    result = {}
    for our_name, (module, json_key) in OFFSET_MAP.items():
        try:
            val = raw_json[module][json_key]
            result[our_name] = int(val)
        except (KeyError, TypeError, ValueError):
            _log(f"missing {our_name} in {module}.{json_key}")
    return result


# ==================== КЭШ ====================
def _load_cache():
    if not os.path.exists(CACHE_FILE):
        return None
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            cache = json.load(f)
        age_h = (time.time() - cache.get("timestamp", 0)) / 3600.0
        if age_h > CACHE_MAX_AGE_H:
            _log(f"cache expired ({age_h:.1f}h)")
            return None
        return cache.get("offsets")
    except Exception as e:
        _log(f"cache read error: {e}")
        return None


def _save_cache(offsets):
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "timestamp": time.time(),
                "offsets": offsets,
            }, f, indent=2)
        _log(f"cache saved → {CACHE_FILE}")
    except Exception as e:
        _log(f"cache write error: {e}")


# ==================== ГЛАВНАЯ ====================
def load_offsets(force_update=False):
    """
    Главная функция — вызывается из test_full.py.

    1. Если force_update=False и есть свежий кэш — используем его.
    2. Иначе — пробуем скачать с GitHub.
    3. Если не вышло — используем fallback (хардкод).
    """
    # 1. Кэш
    if not force_update:
        cached = _load_cache()
        if cached:
            _log("using cached offsets")
            return _merge_with_fallback(cached)

    # 2. Скачивание
    raw = fetch_offsets()
    if raw:
        parsed = parse_offsets(raw)
        if parsed:
            _save_cache(parsed)
            return _merge_with_fallback(parsed)

    # 3. Fallback
    _log("using fallback offsets (hardcoded)")
    return dict(FALLBACK_OFFSETS)


def _merge_with_fallback(new_offsets):
    """
    Скачанные оффсеты НЕ ВСЕГДА содержат всё что нам нужно.
    Например m_iHealth, m_iTeamNum и т.д. в offsets.json НЕТ —
    они в client_dll.json (это схемы, а не global offsets).

    Поэтому: берём скачанные где есть, остальное — fallback.
    """
    result = dict(FALLBACK_OFFSETS)
    result.update(new_offsets)
    return result


# ==================== CLI (для теста) ====================
if __name__ == "__main__":
    print("=== Test offsets_loader ===")
    offs = load_offsets(force_update=True)
    print(f"\nGot {len(offs)} offsets:")
    for k, v in offs.items():
        print(f"  {k} = {hex(v) if isinstance(v, int) else v}")