#!/usr/bin/env python3
"""
update_overlay_bright_switch.py:
Updates romfs_overlay_astc and romfs_overlay_astc_variants/item_notes:
1. Appends dmCustomBrightBoost to ui.json
2. Re-generates videooptionspanelhd.json using touch_layout._custom_page()
3. Updates CFT in vfs-1, re-encodes vfs-1 and vfs-root, updates 00-00000007.data and .build.config
"""

import os
import sys
import struct
import zlib
import hashlib
import json

REPO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO_DIR, "dm", "ios", "tools"))
import touch_layout

def blte_encode(content, block_size=256 * 1024):
    blocks = []
    for start in range(0, len(content), block_size):
        block = content[start:start + block_size]
        blocks.append((b'Z' + zlib.compress(block, 9), block))
    if not blocks:
        blocks.append((b'N', b''))
    header_size = 12 + 24 * len(blocks)
    header = bytearray(b'BLTE' + struct.pack('>I', header_size) + b'\x0f' + len(blocks).to_bytes(3, 'big'))
    for encoded, block in blocks:
        header.extend(struct.pack('>II', len(encoded), len(block)))
        header.extend(hashlib.md5(encoded).digest())
    return bytes(header) + b''.join(encoded for encoded, _ in blocks)

def blte_decode(raw):
    assert raw[:4] == b'BLTE', f'BLTE magic expected, got {raw[:4]}'
    hdr = struct.unpack('>I', raw[4:8])[0]
    nblocks = int.from_bytes(raw[9:12], 'big')
    pos = hdr
    out = bytearray()
    for i in range(nblocks):
        enc, dec = struct.unpack('>II', raw[12+i*24:20+i*24])
        blk = raw[pos:pos+enc]
        out.extend(zlib.decompress(blk[1:]) if blk[0] == 0x5a else blk[1:])
        pos += enc
    return bytes(out)

def set_cft_sizes(tvfs, pos, encoded_size, content_size):
    est_size = struct.unpack('>I', tvfs[42:46])[0]
    idx = 1 if est_size <= 0xff else 2 if est_size <= 0xffff else 3 if est_size <= 0xffffff else 4
    tvfs[pos + 16:pos + 20] = struct.pack('>I', encoded_size)
    tvfs[pos + 20 + idx:pos + 24 + idx] = struct.pack('>I', content_size)

def ekey_for(encoded, chunk, archive, offset):
    header_size = struct.unpack('>I', encoded[4:8])[0] if encoded[:4] == b'BLTE' else len(encoded)
    return hashlib.md5(encoded[:header_size]).digest()[:9] + bytes((chunk, archive)) + offset.to_bytes(5, 'big')

NEW_I18N_ENTRY = json.dumps({
    "id": 300371,
    "Key": "dmCustomBrightBoost",
    "enUS": "3D Brightness Boost (off: authentic)",
    "zhTW": "3D 画面辅助提亮（关闭还原原版暗黑氛围）",
    "deDE": "3D Brightness Boost (off: authentic)",
    "esES": "3D Brightness Boost (off: authentic)",
    "frFR": "3D Brightness Boost (off: authentic)",
    "itIT": "3D Brightness Boost (off: authentic)",
    "koKR": "3D Brightness Boost (off: authentic)",
    "plPL": "3D Brightness Boost (off: authentic)",
    "esMX": "3D Brightness Boost (off: authentic)",
    "jaJP": "3D Brightness Boost (off: authentic)",
    "ptBR": "3D Brightness Boost (off: authentic)",
    "ruRU": "3D Brightness Boost (off: authentic)",
    "zhCN": "3D 画面辅助提亮（关闭还原原版暗黑氛围）"
}, ensure_ascii=False)

def patch_overlay(overlay_dir):
    data_dir = os.path.join(overlay_dir, 'data')
    config_path = os.path.join(data_dir, '.build.config')
    data_7_path = os.path.join(data_dir, '00-00000007.data')
    if not (os.path.isfile(config_path) and os.path.isfile(data_7_path)):
        print(f"Skipping {overlay_dir}: no .build.config or 00-00000007.data")
        return

    print(f"\n--- Patching overlay: {overlay_dir} ---")
    with open(config_path, 'r', encoding='utf-8') as f:
        cfg_lines = f.readlines()
    cfg_values = dict(l.strip().split(' = ', 1) for l in cfg_lines if ' = ' in l and not l.startswith('#'))

    old_vfs_ckey, old_vfs_ekey_str = cfg_values['vfs-1'].split()
    old_vfs_dec, old_vfs_enc = map(int, cfg_values['vfs-1-size'].split())
    old_vfs_ekey = bytes.fromhex(old_vfs_ekey_str)
    old_vfs_off = int.from_bytes(old_vfs_ekey[11:16], 'big')

    with open(data_7_path, 'rb') as f:
        data_7_raw = bytearray(f.read())

    vfs1_raw = data_7_raw[old_vfs_off:old_vfs_off+old_vfs_enc]
    vfs1 = bytearray(blte_decode(vfs1_raw))

    cft_off, cft_size = struct.unpack('>II', vfs1[28:36])
    
    arch7_entries = []
    for p in range(cft_off, cft_off + cft_size, 25):
        if vfs1[p+10] == 7:
            off = int.from_bytes(vfs1[p+11:p+16], 'big')
            enc_sz = struct.unpack('>I', vfs1[p+16:p+20])[0]
            dec_sz = struct.unpack('>I', vfs1[p+21:p+25])[0]
            arch7_entries.append((p, off, enc_sz, dec_sz))

    arch7_entries.sort(key=lambda x: x[1])
    print(f"Total arch 7 entries: {len(arch7_entries)}")

    new_arch7_data = bytearray()
    new_offset = 0
    modified_count = 0

    # Generate brand new VideoOptionsPanelHD.json content
    new_video_page_str, _ = touch_layout._custom_page('"name": "VideoOptionsPanel", "name": "OptionsTable"')
    new_video_page_bytes = new_video_page_str.encode('utf-8')
    assert b'TouchCustom_bright_boost' in new_video_page_bytes

    for idx, (cft_pos, old_off, old_enc, old_dec) in enumerate(arch7_entries):
        raw_chunk = data_7_raw[old_off:old_off+old_enc]
        dec_chunk = zlib.decompress(raw_chunk)

        # 1. Update VideoOptionsPanelHD.json
        if b'TouchCustom_' in dec_chunk and b'VideoOptionsPanel' in dec_chunk:
            print(f"  Entry #{idx} at old_off={old_off}: updating VideoOptionsPanelHD.json ({len(dec_chunk)} -> {len(new_video_page_bytes)} bytes)")
            new_dec = new_video_page_bytes
            new_raw = zlib.compress(new_dec, 9)
            new_enc = len(new_raw)
            new_dec_len = len(new_dec)
            modified_count += 1

        # 2. Update ui.json
        elif b'dmCustomAutoMana' in dec_chunk and dec_chunk.strip().endswith(b']'):
            print(f"  Entry #{idx} at old_off={old_off}: appending dmCustomBrightBoost to ui.json ({len(dec_chunk)} bytes)")
            text = dec_chunk.decode('utf-8')
            if 'dmCustomBrightBoost' not in text:
                end_bracket = text.rindex(']')
                new_text = text[:end_bracket].rstrip().rstrip(',') + ',\n  ' + NEW_I18N_ENTRY + '\n]\n'
                new_dec = new_text.encode('utf-8')
                new_raw = zlib.compress(new_dec, 9)
                new_enc = len(new_raw)
                new_dec_len = len(new_dec)
                modified_count += 1
                print(f"    [OK] ui.json updated ({len(dec_chunk)} -> {len(new_dec)} bytes)")
            else:
                new_dec = dec_chunk
                new_raw = raw_chunk
                new_enc = old_enc
                new_dec_len = old_dec
        else:
            new_dec = dec_chunk
            new_raw = raw_chunk
            new_enc = old_enc
            new_dec_len = old_dec

        # Update vfs1 entry:
        md5_9 = hashlib.md5(new_raw).digest()[:9]
        new_ekey = md5_9 + bytes((0, 7)) + new_offset.to_bytes(5, 'big')
        
        vfs1[cft_pos:cft_pos+16] = new_ekey
        vfs1[cft_pos+16:cft_pos+20] = struct.pack('>I', new_enc)
        vfs1[cft_pos+20] = 0 # spec
        vfs1[cft_pos+21:cft_pos+25] = struct.pack('>I', new_dec_len)

        new_arch7_data.extend(new_raw)
        new_offset += new_enc

    print(f"Total modified entries: {modified_count}. Old arch7 size: {old_vfs_off}, new arch7 size: {new_offset}")

    # Re-encode vfs1
    new_vfs1_enc = blte_encode(bytes(vfs1))
    new_vfs1_ekey = ekey_for(new_vfs1_enc, 0, 7, new_offset)
    print(f"New vfs1 encoded size: {len(new_vfs1_enc)}")

    # Update vfs-root
    old_root_ckey, old_root_ekey_str = cfg_values['vfs-root'].split()
    old_root_dec, old_root_enc = map(int, cfg_values['vfs-root-size'].split())
    old_root_ekey = bytes.fromhex(old_root_ekey_str)
    old_root_off = int.from_bytes(old_root_ekey[11:16], 'big')

    root_raw = data_7_raw[old_root_off:old_root_off+old_root_enc]
    root = bytearray(blte_decode(root_raw))

    hit = root.find(old_vfs_ekey)
    assert hit != -1, 'vfs-root 中未命中旧 vfs-1 EKey'
    root[hit:hit+16] = new_vfs1_ekey
    set_cft_sizes(root, hit, len(new_vfs1_enc), len(vfs1))

    new_root_enc = blte_encode(bytes(root))
    new_root_off = new_offset + len(new_vfs1_enc)
    new_root_ekey = ekey_for(new_root_enc, 0, 7, new_root_off)
    print(f"New root encoded size: {len(new_root_enc)}")

    # Assemble new 00-00000007.data
    new_data_7 = new_arch7_data + new_vfs1_enc + new_root_enc
    with open(data_7_path, 'wb') as f:
        f.write(new_data_7)
    print(f"Saved new 00-00000007.data: {len(new_data_7)} bytes")

    # Update .build.config
    new_cfg_lines = []
    for line in cfg_lines:
        if line.startswith('vfs-1 = '):
            new_cfg_lines.append(f'vfs-1 = {hashlib.md5(bytes(vfs1)).hexdigest()} {new_vfs1_ekey.hex()}\n')
        elif line.startswith('vfs-1-size = '):
            new_cfg_lines.append(f'vfs-1-size = {len(vfs1)} {len(new_vfs1_enc)}\n')
        elif line.startswith('vfs-root = ') or line.startswith('vfs-2 = '):
            key = line.split(' = ')[0]
            new_cfg_lines.append(f'{key} = {hashlib.md5(bytes(root)).hexdigest()} {new_root_ekey.hex()}\n')
        elif line.startswith('vfs-root-size = ') or line.startswith('vfs-2-size = '):
            key = line.split(' = ')[0]
            new_cfg_lines.append(f'{key} = {len(root)} {len(new_root_enc)}\n')
        else:
            new_cfg_lines.append(line)

    with open(config_path, 'w', encoding='utf-8') as f:
        f.writelines(new_cfg_lines)
    print(f"Updated {config_path}")

def main():
    patch_overlay(os.path.join(REPO_DIR, "dm", "data", "overlay_astc_aligned", "romfs_overlay_astc"))
    patch_overlay(os.path.join(REPO_DIR, "dm", "data", "overlay_astc_aligned", "romfs_overlay_astc_variants", "item_notes"))
    print("\n[OK] All overlays updated successfully!")

if __name__ == '__main__':
    main()
