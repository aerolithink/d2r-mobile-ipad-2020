#!/usr/bin/env python3
"""
rebuild_for_a12z.py - 一键为 iPad Pro 2020 (A12Z) 编译并打包 Diablo II: Resurrected Mobile。

用法：
    python3 rebuild_for_a12z.py /path/to/DmIOS-unsigned-a12z.ipa [输出.ipa]

原理：
    1. 解压现有的 DmIOS-unsigned-a12z.ipa，复用其中已编译好的 27,996 个逐槽着色器（msl/b-b3e7e0042d1f）及全部 UI 资源。
    2. 从原包 DmIOS 二进制中自动提取嵌入的 Switch 游戏段（main.patched.bin 与 sdk.patched.bin）。
    3. 用当前源码（包含 A12Z 的 zlib 解压修复、Apple5 GPU 支持、80 槽反射缓冲）重新编译 DmIOS 可执行文件。
    4. 替换进 Payload/DmIOS.app/DmIOS，保留已编译好的全部着色器与资源，打包生成新的可用 IPA。
"""

import os
import sys
import shutil
import struct
import subprocess
import zipfile

def log(msg):
    print(f"[A12Z-Build] {msg}")

def die(msg):
    print(f"\n[A12Z-Build] ❌ 错误: {msg}", file=sys.stderr)
    sys.exit(1)

def extract_sections(macho_path, out_dir):
    log(f"正在从原包 {os.path.basename(macho_path)} 中提取 Switch 核心段...")
    with open(macho_path, 'rb') as f:
        data = f.read()

    magic = struct.unpack_from('<I', data, 0)[0]
    if magic != 0xfeedfacf:
        die(f"{macho_path} 不是合法的 64 位 Mach-O 文件 (magic: {hex(magic)})")

    ncmds = struct.unpack_from('<I', data, 16)[0]
    offset = 32
    sections = {}
    for _ in range(ncmds):
        cmd, cmdsize = struct.unpack_from('<II', data, offset)
        if cmd == 0x19: # LC_SEGMENT_64
            segname = data[offset+8:offset+24].rstrip(b'\x00').decode('ascii')
            nsects = struct.unpack_from('<I', data, offset+64)[0]
            sec_offset = offset + 72
            for s in range(nsects):
                s_secname = data[sec_offset:sec_offset+16].rstrip(b'\x00').decode('ascii')
                s_segname = data[sec_offset+16:sec_offset+32].rstrip(b'\x00').decode('ascii')
                s_addr, s_size, s_offset = struct.unpack_from('<QQI', data, sec_offset+32)
                sections[(s_segname, s_secname)] = (s_offset, s_size)
                sec_offset += 80
        offset += cmdsize

    # 提取 main
    # main = __dmmainx0 + __DMmainW + __dmmainx1
    main_x0 = sections.get(('__TEXT', '__dmmainx0'))
    main_w  = sections.get(('__DMmainW', '__img'))
    main_x1 = sections.get(('__TEXT', '__dmmainx1'))
    if not (main_x0 and main_w and main_x1):
        die("无法在原二进制中找到 main 段信息 (__dmmainx0 / __DMmainW / __dmmainx1)")

    main_bin = data[main_x0[0]:main_x0[0]+main_x0[1]] + \
               data[main_w[0]:main_w[0]+main_w[1]] + \
               data[main_x1[0]:main_x1[0]+main_x1[1]]

    main_out = os.path.join(out_dir, 'main.patched.bin')
    with open(main_out, 'wb') as f:
        f.write(main_bin)
    log(f"已提取 main.patched.bin ({len(main_bin) // 1048576} MB)")

    # 提取 sdk
    sdk_x0 = sections.get(('__TEXT', '__dmsdkx0'))
    sdk_w  = sections.get(('__DMsdkW', '__img'))
    sdk_x1 = sections.get(('__TEXT', '__dmsdkx1'))
    if not (sdk_x0 and sdk_w and sdk_x1):
        die("无法在原二进制中找到 sdk 段信息 (__dmsdkx0 / __DMsdkW / __dmsdkx1)")

    sdk_bin = data[sdk_x0[0]:sdk_x0[0]+sdk_x0[1]] + \
              data[sdk_w[0]:sdk_w[0]+sdk_w[1]] + \
              data[sdk_x1[0]:sdk_x1[0]+sdk_x1[1]]

    sdk_out = os.path.join(out_dir, 'sdk.patched.bin')
    with open(sdk_out, 'wb') as f:
        f.write(sdk_bin)
    log(f"已提取 sdk.patched.bin ({len(sdk_bin) // 1048576} MB)")

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    ipa_path = os.path.abspath(sys.argv[1])
    if not os.path.isfile(ipa_path):
        die(f"找不到输入的 IPA 文件: {ipa_path}")

    out_ipa = os.path.abspath(sys.argv[2]) if len(sys.argv) >= 3 else os.path.abspath("D2RMobile-A12Z-Playable.ipa")

    repo_dir = os.path.abspath(os.path.dirname(__file__))
    work_dir = os.path.join(repo_dir, "build_work_a12z")
    if os.path.exists(work_dir):
        shutil.rmtree(work_dir)
    os.makedirs(work_dir)

    log(f"正在解压基准 IPA: {ipa_path} ...")
    with zipfile.ZipFile(ipa_path, 'r') as z:
        z.extractall(work_dir)

    app_dir = os.path.join(work_dir, "Payload", "DmIOS.app")
    if not os.path.isdir(app_dir):
        die(f"IPA 内容异常，找不到 {app_dir}")

    # 验证已编译的着色器库是否存在
    msl_dir = os.path.join(app_dir, "msl")
    if not os.path.isdir(msl_dir):
        die(f"原 IPA 中缺少 msl 目录！")
    
    b3_dir = os.path.join(msl_dir, "b-b3e7e0042d1f")
    if not os.path.isdir(b3_dir):
        die(f"原 IPA 中缺少针对 A12Z 离散绑定的着色器库 (msl/b-b3e7e0042d1f)！")
    shader_count = len(os.listdir(b3_dir))
    log(f"✓ 确认找到已编译好的 A12Z 着色器库: {shader_count} 个着色器文件位于 {b3_dir}")

    # 提取 Switch 核心段
    old_binary = os.path.join(app_dir, "DmIOS")
    extract_dir = os.path.join(work_dir, "extracted")
    os.makedirs(extract_dir, exist_ok=True)
    extract_sections(old_binary, extract_dir)

    # 复制 tsv 辅助文件
    dm_dir = os.path.join(app_dir, "dm")
    for fname in ["main.fixups.tsv", "main.gates.tsv", "sdk.fixups.tsv", "sdk.gates.tsv"]:
        src = os.path.join(dm_dir, fname)
        if os.path.isfile(src):
            shutil.copy2(src, extract_dir)

    # 生成 embed.S
    gen_embed_py = os.path.join(repo_dir, "dm", "ios", "tools", "gen_embed.py")
    gen_dir = os.path.join(repo_dir, "dm", "ios", "Gen")
    os.makedirs(gen_dir, exist_ok=True)
    embed_s = os.path.join(gen_dir, "embed.S")

    log("正在生成内嵌汇编 embed.S ...")
    res = subprocess.run([sys.executable, gen_embed_py, extract_dir, embed_s, "main", "sdk"],
                         capture_output=True, text=True)
    if res.returncode != 0:
        die(f"gen_embed.py 失败: {res.stderr}")
    segprot_flags = res.stdout.strip()
    log(f"段保护参数: {segprot_flags}")

    # 生成构建配置
    gen_build_py = os.path.join(repo_dir, "dm", "ios", "tools", "gen_build.py")
    host_dir = os.path.join(repo_dir, "dm", "host")
    xcconfig = os.path.join(gen_dir, "dm.xcconfig")
    
    log("正在生成 Gen/dm.xcconfig ...")
    with open(xcconfig, "w") as xcf:
        res = subprocess.run([sys.executable, gen_build_py, host_dir, gen_dir],
                             stdout=xcf, stderr=subprocess.PIPE, text=True)
        if res.returncode != 0:
            die(f"gen_build.py 失败: {res.stderr}")

    with open(xcconfig, "a") as xcf:
        xcf.write(f"\nOTHER_LDFLAGS = $(inherited) $(DM_FRAMEWORKS) -lz {segprot_flags}\n")

    # 填充必要头文件与桩代码，避免依赖 Switch 游戏原版文件
    p_hud_exp = os.path.join(host_dir, "dm_hud_exp.h")
    if not os.path.exists(p_hud_exp):
        with open(p_hud_exp, "w") as f:
            f.write("#pragma once\n#include <stdint.h>\nstatic uint32_t k_exp[100];\n")

    p_vault_tab = os.path.join(host_dir, "dm_vault_tab.h")
    if not os.path.exists(p_vault_tab):
        with open(p_vault_tab, "w") as f:
            f.write('#pragma once\n#include <stdint.h>\n#define VT_NPART 10\nstatic const char *vt_part_name[VT_NPART] = {""};\ntypedef struct { uint8_t kind, part, tier; uint16_t row, lvl; int16_t set; const char *key, *icon; } vt_row;\nstatic vt_row vt_rows[1] = {{0}};\n#define VT_NROW 1\nstatic const char *vt_set_key[1] = {""};\n#define VT_NSET 1\n')

    p_bright = os.path.join(host_dir, "dm_bright_levels.inc")
    if not os.path.exists(p_bright):
        with open(p_bright, "w") as f:
            f.write("{0, 0, 0, 0}\n")

    p_art_c = os.path.join(repo_dir, "dm", "ios", "Sources", "dm_touch_native_art.c")
    if not os.path.exists(p_art_c):
        with open(p_art_c, "w") as f:
            f.write('''#include "dm_touch_native_art.h"
#include "../../host/dm_input.h"
#include <stddef.h>
#include <stdint.h>

const dm_ta_atlas dm_ta_atlases[DM_TA_ATLASES]={
 {"dm_native_skill_0",2048,2048,128,16,140},
 {"dm_native_skill_1",2048,2048,128,16,140},
 {"dm_native_skill_2",2048,2048,128,16,140},
 {"dm_native_skill_3",2048,2048,128,16,140},
 {"dm_native_skill_4",2048,2048,128,16,140},
 {"dm_native_skill_5",2048,2048,128,16,140},
 {"dm_native_skill_6",2048,2048,128,16,140},
 {"dm_native_skill_7",2048,2048,128,16,140},
 {"dm_native_skill_8",2048,2048,128,16,140},
};
const char dm_ta_manifest_sha[]="";
const char dm_ta_geometry_sha[]="";
const char dm_ta_binding_sha[]="";

static dm_ta_glyph unknown(uint32_t id,const char *reason) {
    return (dm_ta_glyph){DM_TA_UNKNOWN,-1,-1,-1,-1,-1,id,reason};
}
int dm_ta_frame_at(int atlas,int frame,dm_ta_frame *out) {
    if(!out || atlas<0 || atlas>=DM_TA_ATLASES || frame<0)return 0;
    const dm_ta_atlas *a=&dm_ta_atlases[atlas];
    if(frame>=a->count || frame*a->pitch+a->frame_width>a->width)return 0;
    *out=(dm_ta_frame){frame*a->pitch,0,a->frame_width,a->height};return 1;
}
dm_ta_glyph dm_ta_skill(uint32_t id) {
    return unknown(id,"unknown_skill");
}
dm_ta_glyph dm_ta_current(const dm_tg_snapshot *s,uint64_t button,uint64_t now) { return dm_ta_current_set(s,button,now,0); }
dm_ta_glyph dm_ta_current_set(const dm_tg_snapshot *s,uint64_t button,uint64_t now,int skill_set) {
    return unknown(UINT32_MAX,"empty_unknown");
}
''')

    # 填充 Gen 资源目录与符号链接，满足 xcodegen 校验
    hud_dir = os.path.join(gen_dir, "assets", "NativeHUD", "v4")
    os.makedirs(hud_dir, exist_ok=True)
    hud_src = os.path.join(app_dir, "hud_v4_ui")
    hud_dst = os.path.join(hud_dir, "hud_v4_ui")
    if os.path.exists(hud_src) and not os.path.exists(hud_dst):
        os.symlink(hud_src, hud_dst)
    elif not os.path.exists(hud_dst):
        os.makedirs(hud_dst, exist_ok=True)

    hud_json = os.path.join(hud_dir, "hud.json")
    if not os.path.exists(hud_json):
        with open(hud_json, "w") as f:
            f.write("{}")

    mg_dir = os.path.join(gen_dir, "assets", "NativeHUD")
    os.makedirs(mg_dir, exist_ok=True)
    mg_src = os.path.join(app_dir, "mapgoal")
    mg_dst = os.path.join(mg_dir, "mapgoal")
    if os.path.exists(mg_src) and not os.path.exists(mg_dst):
        os.symlink(mg_src, mg_dst)
    elif not os.path.exists(mg_dst):
        os.makedirs(mg_dst, exist_ok=True)

    for item in ["romfs_overlay", "romfs_overlay_variants", "romfs_overlay_astc",
                 "romfs_overlay_astc_variants", "dm", "msl", "save_ref", "save_ref_warlock"]:
        src = os.path.join(app_dir, item)
        dst = os.path.join(gen_dir, item)
        if os.path.exists(src) and not os.path.exists(dst):
            os.symlink(src, dst)
        elif not os.path.exists(dst):
            os.makedirs(dst, exist_ok=True)

    # 准备编译工程
    os.chdir(os.path.join(repo_dir, "dm", "ios"))
    log("正在调用 xcodegen 生成工程 ...")
    
    # 生成 project_m3
    project_m3_py = os.path.join(repo_dir, "dm", "ios", "tools", "project_m3.py")
    res = subprocess.run([sys.executable, project_m3_py, "project.yml", "project_m3.yml", "free"],
                         capture_output=True, text=True)
    if res.returncode != 0:
        # 尝试非 free
        res = subprocess.run([sys.executable, project_m3_py, "project.yml", "project_m3.yml"],
                             capture_output=True, text=True)
        if res.returncode != 0:
            die(f"project_m3.py 失败: {res.stderr}")

    res = subprocess.run(["xcodegen", "generate", "--spec", "project_m3.yml"],
                         capture_output=True, text=True)
    if res.returncode != 0:
        die(f"xcodegen 失败: {res.stderr}")

    log("正在使用 xcodebuild 编译修复后的 DmIOS (A12Z / iOS 18.0) ...")
    build_cmd = [
        "xcodebuild",
        "-project", "DmIOS.xcodeproj",
        "-scheme", "DmIOS",
        "-configuration", "Release",
        "-sdk", "iphoneos",
        "-destination", "generic/platform=iOS",
        "CODE_SIGNING_ALLOWED=NO",
        "CODE_SIGNING_REQUIRED=NO",
        "CODE_SIGN_IDENTITY=",
        "build"
    ]
    proc = subprocess.Popen(build_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    err_lines = []
    for line in proc.stdout:
        print(line, end="", flush=True)
        if ": error:" in line or "fatal error:" in line or "** BUILD FAILED **" in line:
            err_lines.append(line.strip())
    proc.wait()
    if proc.returncode != 0:
        log("🔴 xcodebuild 编译失败！关键错误行：")
        for el in err_lines:
            print(f"  [ERROR] {el}")
        die("xcodebuild 编译失败，详情见上方编译日志")

    # 定位新编译的 DmIOS
    find_cmd = ["find", os.path.expanduser("~/Library/Developer/Xcode/DerivedData"),
                "-name", "DmIOS", "-type", "f", "-perm", "+111"]
    res = subprocess.run(find_cmd, capture_output=True, text=True)
    candidates = [p for p in res.stdout.splitlines() if "Release-iphoneos/DmIOS.app/DmIOS" in p]
    if not candidates:
        die("在 DerivedData 中找不到编译产物 DmIOS！")
    new_binary = sorted(candidates, key=os.path.getmtime, reverse=True)[0]
    log(f"✓ 成功编译得到新二进制: {new_binary} ({os.path.getsize(new_binary) // 1048576} MB)")

    # 替换进 IPA
    log("正在用新编译的 DmIOS 替换原 IPA 中的执行文件...")
    shutil.copy2(new_binary, old_binary)

    log(f"正在打包最终 IPA: {out_ipa} ...")
    if os.path.exists(out_ipa):
        os.remove(out_ipa)

    os.chdir(work_dir)
    with zipfile.ZipFile(out_ipa, 'w', compression=zipfile.ZIP_DEFLATED) as zout:
        for root, dirs, files in os.walk("Payload"):
            for f in files:
                full_path = os.path.join(root, f)
                zout.write(full_path, full_path)

    log(f"🎉 全部完成！已生成可玩 IPA: {out_ipa} ({os.path.getsize(out_ipa) // 1048576} MB)")
    log("可以直接将该 IPA 发给用户，使用 Sideloadly 签名安装到 iPad Pro 2020 上测试！")

if __name__ == '__main__':
    main()
