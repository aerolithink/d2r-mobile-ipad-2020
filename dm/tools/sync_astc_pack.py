#!/usr/bin/env python3
"""
sync_astc_pack.py:
精确对齐老机型补充包 (00-00000008.data) 与覆盖层中的 TVFS (00-00000007.data, .build.config, pack.json)。
无论补充包的压缩版本或微小字节数差异如何，本工具扫描补充包内实际的 27,216 个贴图流，
将覆盖层中全部条目的偏移量、压缩尺寸、解压尺寸与校验码无损对齐。
"""

import sys
import os
import struct
import zlib
import hashlib
import json

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

def update_overlay(overlay_dir, pack8_path, data_8, sha256_8):
    data_dir = os.path.join(overlay_dir, 'data')
    config_path = os.path.join(data_dir, '.build.config')
    data_7_path = os.path.join(data_dir, '00-00000007.data')
    if not (os.path.isfile(config_path) and os.path.isfile(data_7_path)):
        print(f'[sync_astc_pack] 跳过（不存在 data/.build.config）: {overlay_dir}')
        return

    print(f'[sync_astc_pack] 正在对齐覆盖层: {overlay_dir} ...')
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
    arch8_entries = []
    for p in range(cft_off, cft_off + cft_size, 25):
        if vfs1[p+10] == 8:
            old_off = int.from_bytes(vfs1[p+11:p+16], 'big')
            expected_dec = struct.unpack('>I', vfs1[p+21:p+25])[0]
            arch8_entries.append((p, old_off, expected_dec))

    arch8_sorted = sorted(arch8_entries, key=lambda x: x[1])

    # 扫描并更新贴图流
    offset = 0
    total_len_8 = len(data_8)
    for idx, (cft_pos, old_off, expected_dec) in enumerate(arch8_sorted):
        d = zlib.decompressobj()
        chunk_len = min(total_len_8 - offset, max(65536, expected_dec + 1024))
        chunk = data_8[offset:offset+chunk_len]
        out = d.decompress(chunk)
        consumed = chunk_len - len(d.unused_data)
        assert len(out) == expected_dec, f'#{idx}: expected {expected_dec}, got {len(out)}'
        md5_9 = hashlib.md5(chunk[:consumed]).digest()[:9]

        new_ekey = md5_9 + bytes((0, 8)) + offset.to_bytes(5, 'big')
        vfs1[cft_pos:cft_pos+16] = new_ekey
        vfs1[cft_pos+16:cft_pos+20] = struct.pack('>I', consumed)
        vfs1[cft_pos+20] = 0 # spec z
        vfs1[cft_pos+21:cft_pos+25] = struct.pack('>I', expected_dec)
        offset += consumed

    assert offset == total_len_8, f'未完整消费补充包: offset={offset}, total={total_len_8}'

    # 重新编码 vfs-1
    new_vfs1_enc = blte_encode(bytes(vfs1))
    new_vfs1_ekey = ekey_for(new_vfs1_enc, 0, 7, old_vfs_off)

    # 更新 vfs-root
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
    new_root_off = old_vfs_off + len(new_vfs1_enc)
    new_root_ekey = ekey_for(new_root_enc, 0, 7, new_root_off)

    # 组装新 00-00000007.data (原前缀 + 新 vfs1 + 新 root)
    new_data_7 = data_7_raw[:old_vfs_off] + new_vfs1_enc + new_root_enc
    with open(data_7_path, 'wb') as f:
        f.write(new_data_7)

    # 更新 .build.config
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

    # 若目录下有 pack.json，同步更新
    pack_json_path = os.path.join(overlay_dir, 'pack.json')
    if os.path.isfile(pack_json_path) or 'romfs_overlay_astc' in os.path.basename(overlay_dir):
        with open(pack_json_path, 'w', encoding='utf-8') as f:
            json.dump({
                "name": "00-00000008.data",
                "size": total_len_8,
                "sha256": sha256_8
            }, f, indent=1)
            f.write('\n')
        print(f'[sync_astc_pack] 已更新: {pack_json_path}')

    print(f'[sync_astc_pack] OK aligned: {overlay_dir} (new 00-00000007.data size: {len(new_data_7)})')

def main():
    if len(sys.argv) < 3:
        print('用法: sync_astc_pack.py <00-00000008.data路径> <覆盖层目录...>')
        sys.exit(1)

    pack8_path = sys.argv[1]
    overlay_dirs = sys.argv[2:]

    print(f'[sync_astc_pack] 正在读取补充包: {pack8_path} ...')
    with open(pack8_path, 'rb') as f:
        data_8 = memoryview(f.read())
    sha256_8 = hashlib.sha256(data_8).hexdigest()
    print(f'[sync_astc_pack] 补充包大小: {len(data_8)} 字节，SHA-256: {sha256_8}')

    for od in overlay_dirs:
        update_overlay(od, pack8_path, data_8, sha256_8)

if __name__ == '__main__':
    main()
