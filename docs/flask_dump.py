#!/usr/bin/env python3
"""Dump the full Flask tuning config from a connected Svalboard over raw HID.

Wire format (flaskproto.js / AdeptProtocol.swift / mad_hid.c):
frame = [cmd, channel, value_id, u16 BE payload, ...zeros] (32 bytes);
cmd 0x08 = get. Unhandled frames come back with cmd byte 0xFF.
"""
import hid, json, sys

VID, PID = 0x303A, 0x4044
USAGE_PAGE, USAGE = 0xFF60, 0x61

CH = dict(meta=0x00, accel=0x10, gestures=0x11, wiggle=0x12, smoothing=0x13,
          dpi=0x14, dragscroll=0x15, csk=0x16, selword=0x17, sentence=0x18,
          leader=0x19, autoscroll=0x1A, automouse=0x1B, wheelchords=0x1C,
          os=0x1D, numword=0x1E, diag=0x1F, combolayers=0x20)

GESTURE_DIRS = ['E', 'SE', 'S', 'SW', 'W', 'NW', 'N', 'NE']

# Minimal QMK keycode namer — enough for typing keys; everything else stays hex.
BASIC = {0x00: 'KC_NO', 0x28: 'ENTER', 0x29: 'ESC', 0x2A: 'BSPC', 0x2B: 'TAB',
         0x2C: 'SPACE', 0x2D: 'MINUS', 0x2E: 'EQUAL', 0x2F: 'LBKT', 0x30: 'RBKT',
         0x31: 'BSLH', 0x33: 'SEMI', 0x34: 'QUOTE', 0x35: 'GRAVE', 0x36: 'COMMA',
         0x37: 'DOT', 0x38: 'SLASH', 0x39: 'CAPS', 0x4A: 'HOME', 0x4B: 'PGUP',
         0x4C: 'DEL', 0x4D: 'END', 0x4E: 'PGDN', 0x4F: 'RIGHT', 0x50: 'LEFT',
         0x51: 'DOWN', 0x52: 'UP'}
for i in range(26):
    BASIC[0x04 + i] = chr(ord('A') + i)
for i in range(9):
    BASIC[0x1E + i] = str(i + 1)
BASIC[0x27] = '0'
for i in range(12):
    BASIC[0x3A + i] = f'F{i+1}'
MODBITS = [(0x0100, 'LCTL'), (0x0200, 'LSFT'), (0x0400, 'LALT'), (0x0800, 'LGUI')]

def kc_name(v):
    if v is None:
        return None
    mods, base = v & 0xFF00, v & 0x00FF
    if mods and not (mods & ~0x1F00):
        names = [n for bit, n in MODBITS if mods & bit]
        right = bool(mods & 0x1000)
        if names and base in BASIC:
            wrap = '+'.join(('R' + n[1:]) if right else n for n in names)
            return f'{wrap}({BASIC[base]})'
    if v in BASIC:
        return BASIC[v]
    return f'0x{v:04X}'

def open_dev():
    for info in hid.enumerate(VID, PID):
        if info['usage_page'] == USAGE_PAGE and info['usage'] == USAGE:
            d = hid.device()
            d.open_path(info['path'])
            return d
    sys.exit('no Svalboard raw-HID interface found (usage page 0xFF60)')

d = open_dev()

def get(ch, vid):
    buf = bytes([0x00, 0x08, ch, vid] + [0] * 29)
    d.write(buf)
    for _ in range(5):
        r = d.read(32, 500)
        if not r:
            return None
        if r[0] == 0xFF:
            return None
        if r[0] == 0x08 and r[1] == ch and r[2] == vid:
            return (r[3] << 8) | r[4]
    return None

def named(v):
    return {'raw': v, 'key': kc_name(v)} if v is not None else None

out = {}
out['protocol_version'] = get(CH['meta'], 0x01)
if out['protocol_version'] is None:
    sys.exit('device did not answer meta/protocol-version — wrong interface?')

out['dragscroll'] = {'divisor': get(CH['dragscroll'], 0x01),
                     'inverted': get(CH['dragscroll'], 0x03),
                     'interval_ms': get(CH['dragscroll'], 0x06),
                     'max_notches': get(CH['dragscroll'], 0x07)}
out['dpi'] = {'left_index': get(CH['dpi'], 0x02), 'right_index': get(CH['dpi'], 0x03),
              'left_cpi': get(CH['dpi'], 0x04), 'right_cpi': get(CH['dpi'], 0x05)}
out['accel'] = {k: get(CH['accel'], v) for k, v in
                dict(enabled=1, takeoff=2, growth=3, offset=4, limit=5).items()}
out['smoothing'] = {k: get(CH['smoothing'], v) for k, v in
                    dict(enabled=1, factor=2, timeout=3).items()}
out['wiggle'] = {k: get(CH['wiggle'], v) for k, v in
                 dict(interval=1, cooldown=2, threshold=3, enabled=4,
                      action=5, set=6, source=7).items()}
out['autoscroll'] = {k: get(CH['autoscroll'], v) for k, v in
                     dict(inverted=1, speed_scale=2, deadzone=3, range=4,
                          state=5, stop_on_key=6).items()}
out['automouse'] = {k: get(CH['automouse'], v) for k, v in
                    dict(enabled=1, timeout_index=2, threshold=3, layer=4).items()}
out['os'] = {k: get(CH['os'], v) for k, v in
             dict(follow=1, mac=2, detected=3).items()}
out['numword'] = {k: get(CH['numword'], v) for k, v in
                  dict(timeout_ms=1, layer=2).items()}
out['selword_mac'] = get(CH['selword'], 0x01)
out['sentence_enabled'] = get(CH['sentence'], 0x01)

out['csk'] = {'enabled': get(CH['csk'], 0x01), 'slots': []}
n = get(CH['csk'], 0x02) or 16
for i in range(n):
    k = get(CH['csk'], 0x10 + i)
    s = get(CH['csk'], 0x30 + i)
    if k or s:
        out['csk']['slots'].append({'slot': i, 'key': named(k), 'shifted': named(s)})

out['leader'] = []
for seq in range(8):
    keys = [get(CH['leader'], 0x10 + seq * 8 + p) for p in range(5)]
    action = get(CH['leader'], 0x10 + seq * 8 + 5)
    if any(keys) or action:
        out['leader'].append({'seq': seq,
                              'keys': [named(k) for k in keys if k],
                              'action': named(action)})

out['gestures'] = {'ratchet': get(CH['gestures'], 0x01),
                   'active_set': get(CH['gestures'], 0x02), 'sets': []}
for s in range(8):
    dirs = {}
    for di, dname in enumerate(GESTURE_DIRS):
        vid = 0x10 + s * 4 + di // 2 if di % 2 == 0 else 0x30 + s * 4 + (di - 1) // 2
        v = get(CH['gestures'], vid)
        if v:
            dirs[dname] = named(v)
    if dirs:
        out['gestures']['sets'].append({'set': s, 'dirs': dirs})

out['wheelchords'] = {'enabled': get(CH['wheelchords'], 0x01),
                      'step': get(CH['wheelchords'], 0x02),
                      'hold_ms': get(CH['wheelchords'], 0x03), 'buttons': []}
for b in range(8):
    dirs = {}
    for di, dname in enumerate(GESTURE_DIRS):
        v = get(CH['wheelchords'], 0x10 + b * 8 + di)
        if v:
            dirs[dname] = named(v)
    if dirs:
        out['wheelchords']['buttons'].append({'button': b + 1, 'dirs': dirs})

out['combo_layer_masks'] = {}
cnt = get(CH['combolayers'], 0x01) or 0
for i in range(min(cnt, 64)):
    m = get(CH['combolayers'], 0x10 + i)
    if m is not None and m != 0xFFFF:
        out['combo_layer_masks'][str(i)] = f'0x{m:04X}'
out['combo_count'] = cnt

d.close()
print(json.dumps(out, indent=2))
