import ctypes
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except:
    try: ctypes.windll.user32.SetProcessDPIAware()
    except: pass

import sys
import os
import time
import math
import random
import struct
import threading
from multiprocessing import Process, Value
import tkinter as tk
from tkinter import ttk

import pymem
import pymem.process
import win32api
import win32con

# --- offsets loader ---
from offsets_loader import load_offsets

# --- tray (опционально) ---
try:
    import pystray
    from PIL import Image, ImageDraw
    HAS_TRAY = True
except ImportError:
    HAS_TRAY = False
    print("[!] pystray/Pillow не установлены — трей отключён")
    print("[!] Установи: pip install pystray Pillow")


# ==================== ОФФСЕТЫ ====================
OFFSETS = load_offsets()

# ==================== КОНСТАНТЫ ====================
EXIT_KEY = win32con.VK_F2
BHOP_KEY = win32con.VK_SPACE
MAX_DIST_FIXED = 4000.0
SPOTTED_GRACE = 0.30

HUMANIZE_REACTION_MS = 120
HUMANIZE_JITTER      = 0.12
HUMANIZE_SMOOTH_VAR  = 0.03

VERSION = "0.3.0"
PROJECT_NAME = "FearlessHack"

# ==================== ОРУЖИЕ ====================
WEAPON_PISTOLS  = {1, 2, 3, 4, 30, 32, 36, 61, 63, 64}
WEAPON_SNIPERS  = {9, 11, 38, 40}
WEAPON_SHOTGUNS = {25, 27, 29, 35}
WEAPON_KNIVES   = {42, 59}
WEAPON_AUTOS    = {7, 8, 10, 13, 16, 39, 60, 17, 19, 23, 24, 26, 33, 34, 14, 28}
TRIGGER_BASE = {
    "pistol": (25, 70), "sniper": (40, 350), "auto": (20, 25),
    "shotgun": (30, 180), "knife": (20, 100), "default": (25, 80),
}

# ==================== SHARED ====================
fov_radius      = Value('i', 250)
smooth          = Value('f', 0.35)
aim_mode        = Value('i', 1)
aim_key         = Value('i', win32con.VK_MENU)
draw_fov        = Value('i', 1)
fov_color       = Value('i', 0x00ff00)
trigger_mode    = Value('i', 0)
trigger_key     = Value('i', 0x06)
trigger_delay   = Value('i', 0)
trigger_cool    = Value('i', 0)
draw_crosshair  = Value('i', 1)
humanize        = Value('i', 1)
aim_zone        = Value('i', 0)

screen_w = Value('i', 0)
screen_h = Value('i', 0)

tray_actions = {"show": False, "exit": False}

# Общая переменная для лог-строки оффсетов в SETTINGS
offsets_status = {"text": "auto-loaded at startup", "color": "#00ff88"}


# ==================== ЧТЕНИЕ ====================
def r_ptr(pm, a):
    try: return pm.read_longlong(a)
    except: return 0
def r_int(pm, a):
    try: return pm.read_int(a)
    except: return 0
def r_uint(pm, a):
    try: return pm.read_uint(a)
    except: return 0
def r_ushort(pm, a):
    try: return pm.read_ushort(a)
    except: return 0
def r_uchar(pm, a):
    try: return pm.read_uchar(a)
    except: return 0
def r_float(pm, a):
    try: return pm.read_float(a)
    except: return 0.0
def r_bool(pm, a):
    try: return pm.read_bool(a)
    except: return False
def r_vec3(pm, a):
    try:
        return (pm.read_float(a), pm.read_float(a+4), pm.read_float(a+8))
    except:
        return (0.0, 0.0, 0.0)


# ==================== МАТЕМАТИКА ====================
def angle_norm(a):
    while a > 180.0:  a -= 360.0
    while a < -180.0: a += 360.0
    return a

def calc_angles(src, dst):
    dx = dst[0] - src[0]; dy = dst[1] - src[1]; dz = dst[2] - src[2]
    hyp = math.sqrt(dx*dx + dy*dy)
    yaw = math.degrees(math.atan2(dy, dx))
    pitch = -math.degrees(math.atan2(dz, hyp))
    return pitch, yaw

def world_to_screen(vm, pos, sw, sh):
    if not vm: return None
    x, y, z = pos
    cx = vm[0]*x + vm[4]*y + vm[8]*z  + vm[12]
    cy = vm[1]*x + vm[5]*y + vm[9]*z  + vm[13]
    cw = vm[3]*x + vm[7]*y + vm[11]*z + vm[15]
    if cw < 0.001: return None
    sx = (sw * 0.5) * (cx / cw) + (sw * 0.5)
    sy = -(sh * 0.5) * (cy / cw) + (sh * 0.5)
    if sx < -2000 or sx > sw + 2000 or sy < -2000 or sy > sh + 2000:
        return None
    return sx, sy


# ==================== ПОЛЕЗНОЕ ====================
def get_pos(pm, pawn):
    p = r_vec3(pm, pawn + OFFSETS["m_vOldOrigin"])
    if p[0] == 0.0 and p[1] == 0.0 and p[2] == 0.0: return None
    return p

def get_eye_pos(pm, pawn, base_pos):
    off = r_vec3(pm, pawn + OFFSETS["m_vecViewOffset"])
    if not (10.0 < off[2] < 100.0):
        off = (0.0, 0.0, 64.0)
    return (base_pos[0] + off[0], base_pos[1] + off[1], base_pos[2] + off[2])

def is_dormant(pm, pawn):
    gsn = r_ptr(pm, pawn + OFFSETS["m_pGameSceneNode"])
    if not gsn: return True
    return r_bool(pm, gsn + OFFSETS["m_bDormant"])

def get_view_angles(pm, client):
    b = client + OFFSETS["dwViewAngles"]
    return r_float(pm, b), r_float(pm, b+4)

def set_view_angles(pm, client, p, y):
    b = client + OFFSETS["dwViewAngles"]
    try:
        pm.write_float(b, p); pm.write_float(b+4, y)
    except: pass

def get_weapon_id(pm, lp, el):
    ws = r_ptr(pm, lp + OFFSETS["m_pWeaponServices"])
    if not ws: return 0
    hw = r_int(pm, ws + OFFSETS["m_hActiveWeapon"])
    if not hw: return 0
    pe = r_ptr(pm, el + 0x10 + 0x8 * ((hw & 0x7FFF) >> 9))
    if not pe: return 0
    wpn = r_ptr(pm, pe + 0x70 * (hw & 0x1FF))
    if not wpn: return 0
    return r_ushort(pm, wpn + OFFSETS["m_AttributeManager"] + OFFSETS["m_Item"] + OFFSETS["m_iItemDefinitionIndex"])

def classify(wid):
    if wid in WEAPON_PISTOLS:  return "pistol"
    if wid in WEAPON_SNIPERS:  return "sniper"
    if wid in WEAPON_SHOTGUNS: return "shotgun"
    if wid in WEAPON_KNIVES:   return "knife"
    if wid in WEAPON_AUTOS:    return "auto"
    return "default"

def mouse_down(): ctypes.windll.user32.mouse_event(0x0002, 0, 0, 0, 0)
def mouse_up():   ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)


def pick_hit_zone():
    if aim_zone.value == 1:
        return "head"
    r = random.randint(1, 100)
    if r <= 50:   return "head"
    elif r <= 90: return "body"
    else:         return "legs"


def get_aim_point(zone, t_eye):
    if zone == "head":
        return (t_eye[0], t_eye[1], t_eye[2] + 4.0)
    elif zone == "body":
        return (t_eye[0], t_eye[1], t_eye[2] - 25.0)
    else:
        return (t_eye[0], t_eye[1], t_eye[2] - 50.0)


# ==================== AIM LOOP ====================
def aim_loop():
    print(f"[+] {PROJECT_NAME} v{VERSION} — AIM LOOP")
    try: pm = pymem.Pymem("cs2.exe")
    except Exception as e:
        print("[-]", e); return
    client = pymem.process.module_from_name(pm.process_handle, "client.dll").lpBaseOfDll
    print(f"[+] client.dll = {hex(client)}")

    sw = screen_w.value; sh = screen_h.value
    if sw == 0 or sh == 0:
        sw = ctypes.windll.user32.GetSystemMetrics(0)
        sh = ctypes.windll.user32.GetSystemMetrics(1)
    cx, cy = sw // 2, sh // 2

    TARGET_DT = 1.0 / 300.0
    next_tick = time.perf_counter()
    locked_pawn = 0
    last_hold = 0.0; was_holding = False; HOLD_GRACE = 0.15
    trig_held = False; trig_press = 0.0; trig_release = 0.0
    bhop_last = 0.0

    current_zone = "head"
    aim_start_time = 0.0
    spotted_cache = {}

    while True:
        now = time.perf_counter()
        if now < next_tick:
            time.sleep(max(0.0, next_tick - now - 0.0003))
            while time.perf_counter() < next_tick: pass
        next_tick = time.perf_counter() + TARGET_DT

        if win32api.GetAsyncKeyState(EXIT_KEY) & 0x8000:
            os._exit(0)

        amode = aim_mode.value
        aim_holding = False
        if amode == 1:
            raw = bool(win32api.GetAsyncKeyState(aim_key.value) & 0x8000)
            if raw:
                last_hold = time.time(); was_holding = True; aim_holding = True
            else:
                if was_holding and (time.time() - last_hold) < HOLD_GRACE:
                    aim_holding = True
                elif was_holding:
                    locked_pawn = 0; was_holding = False
                    aim_start_time = 0.0
        elif amode == 2:
            aim_holding = True

        tmode = trigger_mode.value
        trig_holding = False
        if tmode == 1:
            trig_holding = bool(win32api.GetAsyncKeyState(trigger_key.value) & 0x8000)
        elif tmode == 2:
            trig_holding = True

        lp = r_ptr(pm, client + OFFSETS["dwLocalPlayerPawn"])
        if not lp: continue

        local_team = r_uchar(pm, lp + OFFSETS["m_iTeamNum"])
        local_pos = get_pos(pm, lp)
        if not local_pos: continue

        local_eye = get_eye_pos(pm, lp, local_pos)

        try:
            vm = struct.unpack('<16f', pm.read_bytes(client + OFFSETS["dwViewMatrix"], 64))
        except: continue

        el = r_ptr(pm, client + OFFSETS["dwEntityList"])
        if not el: continue
        le = r_ptr(pm, el + 0x10)
        if not le: continue

        if win32api.GetAsyncKeyState(BHOP_KEY) & 0x8000:
            flags = r_int(pm, lp + OFFSETS["m_fFlags"])
            if flags & 1:
                now_ms = time.time() * 1000.0
                if now_ms - bhop_last > 30:
                    ctypes.windll.user32.keybd_event(BHOP_KEY, 0, 0, 0)
                    ctypes.windll.user32.keybd_event(BHOP_KEY, 0, 2, 0)
                    bhop_last = now_ms

        maddr = lp + OFFSETS["m_entitySpottedState"] + OFFSETS["m_bSpottedByMask"]
        my_m0 = r_uint(pm, maddr); my_m1 = r_uint(pm, maddr + 4)

        candidates = []
        now_time = time.time()

        for i in range(64):
            try:
                ctrl = r_ptr(pm, le + (i + 1) * 0x70)
                if not ctrl: continue
                if r_bool(pm, ctrl + OFFSETS["m_bIsLocalPlayerController"]): continue
                ph = r_int(pm, ctrl + OFFSETS["m_hPawn"])
                if not ph: continue
                pe = r_ptr(pm, el + 0x10 + 0x8 * ((ph & 0x7FFF) >> 9))
                if not pe: continue
                pawn = r_ptr(pm, pe + 0x70 * (ph & 0x1FF))
                if not pawn or pawn == lp: continue

                hp = r_int(pm, pawn + OFFSETS["m_iHealth"])
                if hp <= 0 or hp > 100: continue
                if r_uchar(pm, pawn + OFFSETS["m_lifeState"]) != 0: continue
                if is_dormant(pm, pawn): continue

                team = r_uchar(pm, pawn + OFFSETS["m_iTeamNum"])
                if team not in (2, 3): continue
                if team == local_team: continue

                origin = get_pos(pm, pawn)
                if not origin: continue

                dx_ = origin[0]-local_pos[0]; dy_ = origin[1]-local_pos[1]; dz_ = origin[2]-local_pos[2]
                if math.sqrt(dx_*dx_+dy_*dy_+dz_*dz_) > MAX_DIST_FIXED: continue

                t_eye = get_eye_pos(pm, pawn, origin)
                aim_head = (t_eye[0], t_eye[1], t_eye[2] + 4.0)

                scr_head = world_to_screen(vm, aim_head, sw, sh)
                if not scr_head: continue
                head_score = math.hypot(scr_head[0] - cx, scr_head[1] - cy)
                if head_score > fov_radius.value: continue

                spotted_now = False
                if i < 32:
                    spotted_now = bool(my_m0 & (1 << i))
                else:
                    spotted_now = bool(my_m1 & (1 << (i - 32)))

                if spotted_now:
                    spotted_cache[pawn] = now_time
                last_seen = spotted_cache.get(pawn, 0.0)
                visible = (now_time - last_seen) < SPOTTED_GRACE
                if not visible: continue

                candidates.append({
                    "score": head_score, "pawn": pawn, "t_eye": t_eye,
                })
            except: continue

        for k in list(spotted_cache.keys()):
            if now_time - spotted_cache[k] > 3.0:
                del spotted_cache[k]

        if locked_pawn:
            hpc = r_int(pm, locked_pawn + OFFSETS["m_iHealth"])
            if hpc <= 0 or hpc > 100: locked_pawn = 0

        if locked_pawn and not any(c["pawn"] == locked_pawn for c in candidates):
            locked_pawn = 0

        prev_locked = locked_pawn

        cp_now, cy_now = get_view_angles(pm, client)
        def ang_err(c):
            tp_, ty_ = calc_angles(local_eye, c["t_eye"])
            return abs(angle_norm(tp_ - cp_now)) + abs(angle_norm(ty_ - cy_now))

        best = None
        if candidates:
            for c in candidates:
                if c["pawn"] == locked_pawn: best = c; break
            if not best:
                candidates.sort(key=ang_err)
                best = candidates[0]

        if aim_holding and best and best["score"] <= fov_radius.value:
            if best["pawn"] != prev_locked:
                aim_start_time = time.time()
                current_zone = pick_hit_zone()
            locked_pawn = best["pawn"]

            elapsed_ms = (time.time() - aim_start_time) * 1000.0
            if humanize.value and elapsed_ms < HUMANIZE_REACTION_MS:
                pass
            else:
                aim_point = get_aim_point(current_zone, best["t_eye"])
                tp, ty = calc_angles(local_eye, aim_point)
                cp, cy_ = get_view_angles(pm, client)
                d_p = angle_norm(tp - cp); d_y = angle_norm(ty - cy_)
                s = smooth.value
                if humanize.value:
                    s += random.uniform(-HUMANIZE_SMOOTH_VAR, HUMANIZE_SMOOTH_VAR)
                    if s < 0.05: s = 0.05
                    if s > 1.0: s = 1.0
                new_p = cp + d_p * s
                new_y = angle_norm(cy_ + d_y * s)
                if humanize.value:
                    new_p += random.uniform(-HUMANIZE_JITTER, HUMANIZE_JITTER)
                    new_y += random.uniform(-HUMANIZE_JITTER, HUMANIZE_JITTER)
                if new_p >  89.0: new_p =  89.0
                if new_p < -89.0: new_p = -89.0
                set_view_angles(pm, client, new_p, new_y)
        else:
            if not best:
                locked_pawn = 0

        if trig_holding:
            tidx = r_int(pm, lp + OFFSETS["m_iIDEntIndex"])
            trig_valid = False
            if tidx > 0:
                pe2 = r_ptr(pm, el + 0x10 + 0x8 * ((tidx & 0x7FFF) >> 9))
                if pe2:
                    tpawn = r_ptr(pm, pe2 + 0x70 * (tidx & 0x1FF))
                    if tpawn and tpawn != lp:
                        tt = r_uchar(pm, tpawn + OFFSETS["m_iTeamNum"])
                        thp = r_int(pm, tpawn + OFFSETS["m_iHealth"])
                        if tt in (2, 3) and tt != local_team and 0 < thp <= 100:
                            trig_valid = True
            if trig_valid:
                wid = get_weapon_id(pm, lp, el)
                hms, cms = TRIGGER_BASE.get(classify(wid), TRIGGER_BASE["default"])
                hms += trigger_delay.value
                cms += trigger_cool.value
                now_ms = time.time() * 1000.0
                if not trig_held:
                    if now_ms - trig_release >= cms:
                        mouse_down(); trig_held = True; trig_press = now_ms
                else:
                    if now_ms - trig_press >= hms:
                        mouse_up(); trig_held = False; trig_release = now_ms
            else:
                if trig_held:
                    mouse_up(); trig_held = False; trig_release = time.time() * 1000.0
        else:
            if trig_held:
                mouse_up(); trig_held = False; trig_release = time.time() * 1000.0


# ==================== OVERLAY ====================
def run_overlay(fov_val, draw_val, color_val, cross_val, scr_w, scr_h):
    try: ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except: pass

    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.attributes("-transparentcolor", "black")
    root.configure(bg="black")

    w = scr_w.value; h = scr_h.value
    root.geometry(f"{w}x{h}+0+0")
    canvas = tk.Canvas(root, width=w, height=h, bg="black", highlightthickness=0)
    canvas.pack()

    root.update_idletasks()
    hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
    GWL_EXSTYLE = -20
    ex = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    ctypes.windll.user32.SetWindowLongW(
        hwnd, GWL_EXSTYLE, ex | 0x00000020 | 0x00080000 | 0x00000080)

    cxx, cyy = w // 2, h // 2

    def tick():
        try:
            canvas.delete("all")
            if draw_val.value:
                r = fov_val.value
                canvas.create_oval(cxx-r, cyy-r, cxx+r, cyy+r,
                                   outline=f"#{color_val.value:06x}", width=2)
            if cross_val.value:
                cl = 6; gp = 3; th = 1; cc = "#00ff00"
                canvas.create_line(cxx, cyy-gp-cl, cxx, cyy-gp, fill=cc, width=th)
                canvas.create_line(cxx, cyy+gp, cxx, cyy+gp+cl, fill=cc, width=th)
                canvas.create_line(cxx-gp-cl, cyy, cxx-gp, cyy, fill=cc, width=th)
                canvas.create_line(cxx+gp, cyy, cxx+gp+cl, cyy, fill=cc, width=th)
                canvas.create_oval(cxx-1, cyy-1, cxx+1, cyy+1, fill=cc, outline=cc)
        except: pass
        root.after(16, tick)

    tick()
    root.mainloop()


# ==================== WAIT FOR CS2 ====================
def is_cs2_running():
    try:
        p = pymem.Pymem("cs2.exe")
        try: p.close_process()
        except: pass
        return True
    except:
        return False


def wait_for_cs2():
    root = tk.Tk()
    root.title(f"{PROJECT_NAME} — Waiting")
    root.geometry("360x180")
    root.configure(bg="#141414")
    root.resizable(False, False)
    root.attributes("-topmost", True)

    root.update_idletasks()
    sw = root.winfo_screenwidth(); sh = root.winfo_screenheight()
    root.geometry(f"360x180+{(sw-360)//2}+{(sh-180)//2}")

    root.update_idletasks()
    try:
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        DWMWA_WINDOW_CORNER_PREFERENCE = 33
        DWMWCP_DONOTROUND = 1
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_WINDOW_CORNER_PREFERENCE,
            ctypes.byref(ctypes.c_int(DWMWCP_DONOTROUND)), 4)
    except: pass

    tk.Label(root, text=f"{PROJECT_NAME}", fg="#00d4ff", bg="#141414",
             font=("Arial", 18, "bold")).pack(pady=(20, 4))
    tk.Label(root, text=f"v{VERSION}", fg="#666666", bg="#141414",
             font=("Arial", 9)).pack()

    status_lbl = tk.Label(root, text="Waiting for CS2...",
                          fg="#ffaa00", bg="#141414",
                          font=("Arial", 11, "italic"))
    status_lbl.pack(pady=(20, 4))

    hint_lbl = tk.Label(root, text="Запусти Counter-Strike 2",
                        fg="#888888", bg="#141414", font=("Arial", 9))
    hint_lbl.pack()

    result = {"ok": False, "closed": False}

    def on_close():
        result["closed"] = True
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)

    def check():
        if result["closed"]:
            return
        if is_cs2_running():
            result["ok"] = True
            status_lbl.config(text="CS2 found! Starting...", fg="#00ff88")
            root.update()
            time.sleep(0.4)
            root.destroy()
            return
        dots = "." * ((int(time.time() * 2) % 4) + 1)
        status_lbl.config(text=f"Waiting for CS2{dots}")
        root.after(500, check)

    root.after(300, check)
    root.mainloop()
    return result["ok"]


# ==================== MONITOR CS2 ====================
def monitor_cs2():
    while True:
        time.sleep(2.0)
        if not is_cs2_running():
            print("[+] CS2 closed — exiting")
            os._exit(0)


# ==================== TRAY ====================
def make_tray_icon_image():
    img = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([2, 2, 62, 62], fill='#00d4ff')
    d.ellipse([8, 8, 56, 56], fill='#141414')
    d.line([20, 20, 20, 44], fill='#00d4ff', width=4)
    d.line([20, 20, 32, 20], fill='#00d4ff', width=4)
    d.line([20, 30, 30, 30], fill='#00d4ff', width=4)
    d.line([38, 20, 38, 44], fill='#00d4ff', width=4)
    d.line([50, 20, 50, 44], fill='#00d4ff', width=4)
    d.line([38, 32, 50, 32], fill='#00d4ff', width=4)
    return img


def run_tray():
    if not HAS_TRAY:
        return
    img = make_tray_icon_image()

    def on_show(icon, item):
        tray_actions["show"] = True

    def on_exit(icon, item):
        tray_actions["exit"] = True

    menu = pystray.Menu(
        pystray.MenuItem("Show", on_show, default=True),
        pystray.MenuItem("Exit", on_exit),
    )
    icon = pystray.Icon(PROJECT_NAME, img, f"{PROJECT_NAME} v{VERSION}", menu)
    icon.run()


# ==================== MENU ====================
KEY_PRESETS = [
    ("Alt", win32con.VK_MENU), ("Shift", win32con.VK_SHIFT),
    ("Ctrl", win32con.VK_CONTROL), ("Mouse4", 0x05), ("Mouse5", 0x06),
    ("V", 0x56), ("C", 0x43), ("X", 0x58),
]


def make_scale(parent, label, var, lo, hi, res, cast=float, hint=None):
    tk.Label(parent, text=label, fg="white", bg="#141414",
             font=("Arial", 10)).pack(anchor="w", padx=15, pady=(8,0))
    tk.Scale(parent, from_=lo, to=hi, resolution=res, orient="horizontal",
             bg="#141414", fg="white", troughcolor="#333",
             highlightthickness=0,
             command=lambda v: setattr(var, 'value', cast(v))).pack(fill="x", padx=15)
    if hint:
        tk.Label(parent, text=hint, fg="#888888", bg="#141414",
                 font=("Arial", 8, "italic")).pack(anchor="w", padx=15)


def make_toggle(parent, var, name, width=12):
    btn = tk.Button(parent, text=f"{name}: {'ON' if var.value else 'OFF'}", width=width)
    def cb():
        var.value = 0 if var.value else 1
        btn.config(text=f"{name}: {'ON' if var.value else 'OFF'}")
    btn.config(command=cb)
    return btn


def make_key_row(parent, key_var, label_text, label_color="#ffaa00"):
    tk.Label(parent, text=label_text, fg="#00ffff", bg="#141414",
             font=("Arial", 10, "bold")).pack(anchor="w", padx=15, pady=(8,0))
    row = tk.Frame(parent, bg="#141414"); row.pack(fill="x", padx=15)
    lbl = tk.Label(parent, text="Alt", fg=label_color, bg="#141414",
                   font=("Arial", 9, "italic"))
    lbl.pack(anchor="w", padx=15)

    def set_key(vk, n):
        key_var.value = vk
        lbl.config(text=n)

    current = key_var.value
    for name, vk in KEY_PRESETS:
        if vk == current:
            lbl.config(text=name); break

    for name, vk in KEY_PRESETS:
        tk.Button(row, text=name, width=6,
                  command=lambda vk=vk, n=name: set_key(vk, n)).pack(side="left", padx=1)


def run_menu():
    root = tk.Tk()
    root.title(f"{PROJECT_NAME} v{VERSION}")
    root.geometry("460x640")
    root.configure(bg="#141414")
    root.attributes("-topmost", True)

    root.update_idletasks()
    try:
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        DWMWA_WINDOW_CORNER_PREFERENCE = 33
        DWMWCP_DONOTROUND = 1
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_WINDOW_CORNER_PREFERENCE,
            ctypes.byref(ctypes.c_int(DWMWCP_DONOTROUND)), 4)
    except: pass

    style = ttk.Style()
    try: style.theme_use('default')
    except: pass
    style.configure('TNotebook', background='#141414', borderwidth=0)
    style.configure('TNotebook.Tab', background='#222222', foreground='white',
                    padding=[18, 8], font=("Arial", 10, "bold"))
    style.map('TNotebook.Tab',
              background=[('selected', '#00d4ff')],
              foreground=[('selected', 'black')])

    notebook = ttk.Notebook(root)
    notebook.pack(fill="both", expand=True, padx=8, pady=8)

    tab_aim = tk.Frame(notebook, bg="#141414")
    tab_trigger = tk.Frame(notebook, bg="#141414")
    tab_settings = tk.Frame(notebook, bg="#141414")
    notebook.add(tab_aim, text="AIM")
    notebook.add(tab_trigger, text="TRIGGER")
    notebook.add(tab_settings, text="SETTINGS")

    # --- TAB AIM ---
    tk.Label(tab_aim, text="AIM MODE", fg="#00ffff", bg="#141414",
             font=("Arial", 11, "bold")).pack(anchor="w", padx=15, pady=(6,0))
    mf = tk.Frame(tab_aim, bg="#141414"); mf.pack(fill="x", padx=15)
    aim_lbl = tk.Label(tab_aim, text="MODE: HOLD", fg="#00ff88", bg="#141414",
                       font=("Arial", 9, "italic"))
    aim_lbl.pack(anchor="w", padx=15)
    def set_aim_mode(m):
        aim_mode.value = m
        aim_lbl.config(text=f"MODE: {['OFF','HOLD','ALWAYS'][m]}",
                       fg=["gray","#00ff88","#ff8800"][m])
    for txt, m in [("OFF", 0), ("HOLD", 1), ("ALWAYS", 2)]:
        tk.Button(mf, text=txt, command=lambda m=m: set_aim_mode(m), width=8).pack(side="left", padx=2)

    make_key_row(tab_aim, aim_key, "AIM KEY")

    tk.Label(tab_aim, text="AIM ZONE", fg="#ffcc00", bg="#141414",
             font=("Arial", 11, "bold")).pack(anchor="w", padx=15, pady=(10,0))
    zf = tk.Frame(tab_aim, bg="#141414"); zf.pack(fill="x", padx=15)
    zone_lbl = tk.Label(tab_aim, text="LEGIT (50% head / 40% body / 10% legs)",
                        fg="#00ff88", bg="#141414", font=("Arial", 9, "italic"))
    zone_lbl.pack(anchor="w", padx=15)
    def set_zone(z):
        aim_zone.value = z
        if z == 0:
            zone_lbl.config(text="LEGIT (50% head / 40% body / 10% legs)", fg="#00ff88")
        else:
            zone_lbl.config(text="RAGNA (100% head)", fg="#ff5555")
    tk.Button(zf, text="LEGIT", command=lambda: set_zone(0), width=10).pack(side="left", padx=2)
    tk.Button(zf, text="RAGNA", command=lambda: set_zone(1), width=10).pack(side="left", padx=2)

    tk.Label(tab_aim, text="HUMANIZE", fg="#ff66cc", bg="#141414",
             font=("Arial", 11, "bold")).pack(anchor="w", padx=15, pady=(10,0))
    fh = tk.Frame(tab_aim, bg="#141414"); fh.pack(fill="x", padx=15, pady=3)
    make_toggle(fh, humanize, "HUMAN").pack(side="left", padx=2)
    tk.Label(tab_aim, text="120мс задержка + jitter 0.12 + random smooth",
             fg="#888888", bg="#141414", font=("Arial", 8, "italic")).pack(anchor="w", padx=15)

    make_scale(tab_aim, "FOV Radius", fov_radius, 10, 800, 5, int, "легит: 80-150")
    make_scale(tab_aim, "Smooth", smooth, 0.05, 1.0, 0.05, float, "легит: 0.30-0.50")

    fa = tk.Frame(tab_aim, bg="#141414"); fa.pack(fill="x", padx=15, pady=(12,3))
    make_toggle(fa, draw_fov, "FOV").pack(side="left", padx=2)
    make_toggle(fa, draw_crosshair, "CROSS").pack(side="left", padx=2)

    # --- TAB TRIGGER ---
    tk.Label(tab_trigger, text="TRIGGER MODE", fg="#00ffff", bg="#141414",
             font=("Arial", 11, "bold")).pack(anchor="w", padx=15, pady=(6,0))
    tf = tk.Frame(tab_trigger, bg="#141414"); tf.pack(fill="x", padx=15)
    trig_lbl = tk.Label(tab_trigger, text="TRIGGER: OFF", fg="gray", bg="#141414",
                        font=("Arial", 9, "italic"))
    trig_lbl.pack(anchor="w", padx=15)
    def set_trig_mode(m):
        trigger_mode.value = m
        trig_lbl.config(text=f"TRIGGER: {['OFF','HOLD','ALWAYS'][m]}",
                        fg=["gray","#ff5555","#ff8800"][m])
    for txt, m in [("OFF", 0), ("HOLD", 1), ("ALWAYS", 2)]:
        tk.Button(tf, text=txt, command=lambda m=m: set_trig_mode(m), width=8).pack(side="left", padx=2)

    make_key_row(tab_trigger, trigger_key, "TRIGGER KEY")

    make_scale(tab_trigger, "Trigger Delay (ms)", trigger_delay, 0, 500, 5, int, "легит: 50-100")
    make_scale(tab_trigger, "Trigger Cooldown (ms)", trigger_cool, 0, 500, 5, int, "легит: 30-100")

    # --- TAB SETTINGS ---
    tk.Label(tab_settings, text=f"{PROJECT_NAME}", fg="#00d4ff", bg="#141414",
             font=("Arial", 16, "bold")).pack(pady=(20,2))
    tk.Label(tab_settings, text=f"Version {VERSION}", fg="white", bg="#141414",
             font=("Arial", 10)).pack()

    tk.Label(tab_settings, text="Hotkeys", fg="#00ffff", bg="#141414",
             font=("Arial", 12, "bold")).pack(anchor="w", padx=15, pady=(20,5))
    tk.Label(tab_settings, text="F2     — выход", fg="white", bg="#141414",
             font=("Arial", 10)).pack(anchor="w", padx=15)
    tk.Label(tab_settings, text="Insert — скрыть/показать меню", fg="white", bg="#141414",
             font=("Arial", 10)).pack(anchor="w", padx=15)

    tk.Label(tab_settings, text="Offsets", fg="#00ffff", bg="#141414",
             font=("Arial", 12, "bold")).pack(anchor="w", padx=15, pady=(20,5))

    off_status_lbl = tk.Label(tab_settings, text=offsets_status["text"],
                              fg=offsets_status["color"], bg="#141414",
                              font=("Arial", 9, "italic"))
    off_status_lbl.pack(anchor="w", padx=15)

    def on_update_offsets():
        off_status_lbl.config(text="Updating...", fg="#ffaa00")
        root.update()

        def worker():
            try:
                new_offs = load_offsets(force_update=True)
                OFFSETS.update(new_offs)
                # найти сколько dw_ полей загружено
                dw_count = sum(1 for k in new_offs if k.startswith("dw"))
                offsets_status["text"] = f"updated: {dw_count} dw* offsets"
                offsets_status["color"] = "#00ff88"
                off_status_lbl.config(text=offsets_status["text"], fg="#00ff88")
                print(f"[OFFSETS] manual update: {dw_count} dw* offsets applied")
            except Exception as e:
                offsets_status["text"] = f"update failed: {e}"
                offsets_status["color"] = "#ff8888"
                off_status_lbl.config(text=offsets_status["text"], fg="#ff8888")
                print(f"[OFFSETS] manual update error: {e}")

        threading.Thread(target=worker, daemon=True).start()

    tk.Button(tab_settings, text="Update Offsets", width=18,
              command=on_update_offsets).pack(anchor="w", padx=15, pady=(6,0))

    tk.Label(tab_settings, text="BHOP", fg="#00ff88", bg="#141414",
             font=("Arial", 12, "bold")).pack(anchor="w", padx=15, pady=(20,5))
    tk.Label(tab_settings, text="Всегда ON, клавиша Space", fg="white", bg="#141414",
             font=("Arial", 10)).pack(anchor="w", padx=15)

    tray_status = "Tray: enabled" if HAS_TRAY else "Tray: disabled (pip install pystray Pillow)"
    tray_color = "#00ff88" if HAS_TRAY else "#ff8888"
    tk.Label(tab_settings, text=tray_status, fg=tray_color, bg="#141414",
             font=("Arial", 9, "italic")).pack(anchor="w", padx=15, pady=(10,0))

    tk.Label(tab_settings, text="Info", fg="#00ffff", bg="#141414",
             font=("Arial", 12, "bold")).pack(anchor="w", padx=15, pady=(20,5))
    tk.Label(tab_settings, text="Запускай CS2 с -allow_third_party_software",
             fg="white", bg="#141414", font=("Arial", 10)).pack(anchor="w", padx=15)
    tk.Label(tab_settings, text="Использовать на свой риск (VAC ban)",
             fg="#ff8888", bg="#141414", font=("Arial", 10)).pack(anchor="w", padx=15)
    tk.Label(tab_settings, text="CS2 закрыт → чит автоматически выйдет",
             fg="white", bg="#141414", font=("Arial", 10)).pack(anchor="w", padx=15, pady=(8,0))

    # --- status bar ---
    status = tk.Frame(root, bg="#0a0a0a", height=22)
    status.pack(fill="x", side="bottom")
    tk.Label(status, text=f"{PROJECT_NAME} v{VERSION} | READY | CS2 monitor: ON",
             fg="#00ff88", bg="#0a0a0a", font=("Arial", 8)).pack(side="left", padx=8)

    # --- global keys ---
    hidden = {"v": False}
    def poll_global():
        last = hidden.get("_last_insert", False)
        s = bool(win32api.GetAsyncKeyState(win32con.VK_INSERT) & 0x8000)
        if s and not last:
            if hidden["v"]:
                try: root.deiconify()
                except: pass
                hidden["v"] = False
            else:
                try: root.withdraw()
                except: pass
                hidden["v"] = True
        hidden["_last_insert"] = s

        if tray_actions["show"]:
            tray_actions["show"] = False
            try: root.deiconify()
            except: pass
            hidden["v"] = False

        if tray_actions["exit"]:
            tray_actions["exit"] = False
            os._exit(0)

        root.after(50, poll_global)

    root.after(200, poll_global)
    root.mainloop()


# ==================== MAIN ====================
if __name__ == "__main__":
    try: ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except: pass

    screen_w.value = ctypes.windll.user32.GetSystemMetrics(0)
    screen_h.value = ctypes.windll.user32.GetSystemMetrics(1)

    print(f"[+] {PROJECT_NAME} v{VERSION}")
    print(f"[+] Screen: {screen_w.value}x{screen_h.value}")

    if not wait_for_cs2():
        print("[-] Waiting cancelled")
        sys.exit(0)

    print("[+] CS2 found — starting modules")

    overlay = Process(target=run_overlay,
                      args=(fov_radius, draw_fov, fov_color,
                            draw_crosshair, screen_w, screen_h))
    overlay.start()

    threading.Thread(target=aim_loop, daemon=True).start()
    threading.Thread(target=monitor_cs2, daemon=True).start()

    if HAS_TRAY:
        threading.Thread(target=run_tray, daemon=True).start()
        print("[+] Tray started")
    else:
        print("[!] Tray disabled")

    try:
        run_menu()
    except: pass

    overlay.terminate()
    print("[+] Stopped")