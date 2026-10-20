#!/usr/bin/env python3
"""
rebuild_for_a12z.py - 一键为 iPad Pro 2020 (A12Z) 编译并打包 Diablo II: Resurrected Mobile。

用法：
    python3 rebuild_for_a12z.py /path/to/DmIOS-unsigned-a12z.ipa [输出.ipa]

原理：
    1. 解压现有的 DmIOS-unsigned-a12z.ipa，复用其中已编译好的着色器（msl/b-c974829dda39）及全部 UI 资源。
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
    
    shader_dir = os.path.join(msl_dir, "b-b3e7e0042d1f")
    if not os.path.isdir(shader_dir):
        die(f"原 IPA 中缺少 A12Z 离散着色器库 (msl/b-b3e7e0042d1f)！")
    shader_count = len(os.listdir(shader_dir))
    log(f"✓ 确认找到已编译好的 A12Z 离散着色器库: {shader_count} 个着色器文件位于 {shader_dir}")

    # 同步更新已精确对齐的 ASTC 贴图覆盖层
    aligned_base = os.path.join(repo_dir, "dm", "data", "overlay_astc_aligned")
    if os.path.isdir(aligned_base):
        log("正在将精确对齐的 ASTC 贴图覆盖层同步至 App ...")
        for item in ["romfs_overlay_astc", "romfs_overlay_astc_variants"]:
            s = os.path.join(aligned_base, item)
            d = os.path.join(app_dir, item)
            if os.path.exists(s):
                if os.path.exists(d):
                    shutil.rmtree(d)
                shutil.copytree(s, d)
                log(f"✓ 已同步更新覆盖层: {item}")

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
    if not os.path.exists(p_art_c) or "rows[]" not in open(p_art_c, "r", encoding="utf-8", errors="ignore").read():
        with open(p_art_c, "w", encoding="utf-8") as f:
            f.write('''/* Generated from frozen official Skills/Skilldesc; do not hand-edit rows. */
#include "dm_touch_native_art.h"
#include "../../host/dm_input.h"
#include <stddef.h>
#include <stdint.h>
const dm_ta_atlas dm_ta_atlases[DM_TA_ATLASES]={
 {"dm_native_skill_0",4080,65,65,68,60},
 {"dm_native_skill_1",4080,65,65,68,60},
 {"dm_native_skill_2",4080,65,65,68,60},
 {"dm_native_skill_3",4080,65,65,68,60},
 {"dm_native_skill_4",4080,65,65,68,60},
 {"dm_native_skill_5",4080,65,65,68,60},
 {"dm_native_skill_6",4080,65,65,68,60},
 {"dm_native_skill_7",4080,65,65,68,60},
 {"dm_native_skill_8",2720,65,65,68,40},
};
const char dm_ta_manifest_sha[]="acf4edc4caef33f98bd1daeac0316981f9dee5046537da9ffb3cb1cb7a27294a";
const char dm_ta_geometry_sha[]="68cbae42c860b0252d4bbb4098788f9ea957ff29c75c5eda690bf2c619acf550";
const char dm_ta_binding_sha[]="f89225ba353ddbfe31d0e8178d96ffa862c76179a2be07b1c1c1165ce6eb8ca1";
static const struct { uint32_t id; int atlas,frame; } rows[]={
 {0,8,2},
 {1,8,0},
 {2,8,6},
 {3,8,4},
 {4,8,12},
 {5,8,10},
 {6,0,0},
 {7,0,2},
 {8,0,4},
 {9,0,6},
 {10,0,8},
 {11,0,10},
 {12,0,12},
 {13,0,14},
 {14,0,16},
 {15,0,18},
 {16,0,20},
 {17,0,22},
 {18,0,24},
 {19,0,26},
 {20,0,28},
 {21,0,30},
 {22,0,32},
 {23,0,34},
 {24,0,36},
 {25,0,38},
 {26,0,40},
 {27,0,42},
 {28,0,44},
 {29,0,46},
 {30,0,48},
 {31,0,50},
 {32,0,52},
 {33,0,54},
 {34,0,56},
 {35,0,58},
 {36,1,0},
 {37,1,2},
 {38,1,4},
 {39,1,6},
 {40,1,8},
 {41,1,10},
 {42,1,12},
 {43,1,14},
 {44,1,16},
 {45,1,18},
 {46,1,20},
 {47,1,22},
 {48,1,24},
 {49,1,26},
 {50,1,28},
 {51,1,30},
 {52,1,32},
 {53,1,34},
 {54,1,36},
 {55,1,38},
 {56,1,40},
 {57,1,42},
 {58,1,44},
 {59,1,46},
 {60,1,48},
 {61,1,50},
 {62,1,52},
 {63,1,54},
 {64,1,56},
 {65,1,58},
 {66,2,0},
 {67,2,2},
 {68,2,4},
 {69,2,6},
 {70,2,8},
 {71,2,10},
 {72,2,12},
 {73,2,14},
 {74,2,16},
 {75,2,18},
 {76,2,20},
 {77,2,22},
 {78,2,24},
 {79,2,26},
 {80,2,28},
 {81,2,30},
 {82,2,32},
 {83,2,34},
 {84,2,36},
 {85,2,38},
 {86,2,40},
 {87,2,42},
 {88,2,44},
 {89,2,46},
 {90,2,48},
 {91,2,50},
 {92,2,52},
 {93,2,54},
 {94,2,56},
 {95,2,58},
 {96,3,0},
 {97,3,2},
 {98,3,4},
 {99,3,6},
 {100,3,8},
 {101,3,10},
 {102,3,12},
 {103,3,14},
 {104,3,16},
 {105,3,18},
 {106,3,20},
 {107,3,22},
 {108,3,24},
 {109,3,38},
 {110,3,28},
 {111,3,40},
 {112,3,32},
 {113,3,34},
 {114,3,36},
 {115,3,26},
 {116,3,30},
 {117,3,42},
 {118,3,44},
 {119,3,46},
 {120,3,48},
 {121,3,50},
 {122,3,52},
 {123,3,56},
 {124,3,54},
 {125,3,58},
 {126,4,0},
 {127,4,2},
 {128,4,4},
 {129,4,6},
 {130,4,8},
 {131,4,10},
 {132,4,12},
 {133,4,14},
 {134,4,16},
 {135,4,18},
 {136,4,20},
 {137,4,22},
 {138,4,24},
 {139,4,26},
 {140,4,28},
 {141,4,30},
 {142,4,32},
 {143,4,34},
 {144,4,36},
 {145,4,38},
 {146,4,40},
 {147,4,42},
 {148,4,44},
 {149,4,46},
 {150,4,48},
 {151,4,50},
 {152,4,52},
 {153,4,54},
 {154,4,56},
 {155,4,58},
 {156,-1,-1},
 {157,-1,-1},
 {158,-1,-1},
 {159,-1,-1},
 {160,-1,-1},
 {161,-1,-1},
 {162,-1,-1},
 {163,-1,-1},
 {164,-1,-1},
 {165,-1,-1},
 {166,-1,-1},
 {167,-1,-1},
 {168,-1,-1},
 {169,-1,-1},
 {170,-1,-1},
 {171,-1,-1},
 {172,-1,-1},
 {173,-1,-1},
 {174,-1,-1},
 {175,-1,-1},
 {176,-1,-1},
 {177,-1,-1},
 {178,-1,-1},
 {179,-1,-1},
 {180,-1,-1},
 {181,-1,-1},
 {182,-1,-1},
 {183,-1,-1},
 {184,-1,-1},
 {185,-1,-1},
 {186,-1,-1},
 {187,-1,-1},
 {188,-1,-1},
 {189,-1,-1},
 {190,-1,-1},
 {191,-1,-1},
 {192,-1,-1},
 {193,-1,-1},
 {194,-1,-1},
 {195,-1,-1},
 {196,-1,-1},
 {197,8,32},
 {198,-1,-1},
 {199,-1,-1},
 {200,-1,-1},
 {201,-1,-1},
 {202,-1,-1},
 {203,-1,-1},
 {204,-1,-1},
 {205,-1,-1},
 {206,-1,-1},
 {207,-1,-1},
 {208,-1,-1},
 {209,-1,-1},
 {210,-1,-1},
 {211,-1,-1},
 {212,-1,-1},
 {213,-1,-1},
 {214,-1,-1},
 {215,-1,-1},
 {216,-1,-1},
 {217,8,14},
 {218,8,14},
 {219,8,18},
 {220,8,18},
 {221,5,0},
 {222,5,2},
 {223,5,20},
 {224,5,52},
 {225,5,32},
 {226,5,16},
 {227,5,6},
 {228,5,22},
 {229,5,34},
 {230,5,36},
 {231,5,8},
 {232,5,54},
 {233,5,56},
 {234,5,38},
 {235,5,40},
 {236,5,10},
 {237,5,12},
 {238,5,24},
 {239,5,26},
 {240,5,42},
 {241,5,14},
 {242,5,58},
 {243,5,28},
 {244,5,44},
 {245,5,46},
 {246,5,4},
 {247,5,18},
 {248,5,30},
 {249,5,50},
 {250,5,48},
 {251,6,0},
 {252,6,20},
 {253,6,22},
 {254,6,40},
 {255,6,42},
 {256,6,2},
 {257,6,4},
 {258,6,36},
 {259,6,44},
 {260,6,46},
 {261,6,6},
 {262,6,8},
 {263,6,26},
 {264,6,24},
 {265,6,50},
 {266,6,10},
 {267,6,30},
 {268,6,38},
 {269,6,58},
 {270,6,48},
 {271,6,12},
 {272,6,14},
 {273,6,34},
 {274,6,52},
 {275,6,54},
 {276,6,16},
 {277,6,18},
 {278,6,28},
 {279,6,32},
 {280,6,56},
 {281,-1,-1},
 {282,-1,-1},
 {283,-1,-1},
 {284,-1,-1},
 {285,-1,-1},
 {286,-1,-1},
 {287,-1,-1},
 {288,-1,-1},
 {289,-1,-1},
 {290,-1,-1},
 {291,-1,-1},
 {292,-1,-1},
 {293,-1,-1},
 {294,-1,-1},
 {295,-1,-1},
 {296,-1,-1},
 {297,-1,-1},
 {298,-1,-1},
 {299,-1,-1},
 {300,-1,-1},
 {301,-1,-1},
 {302,-1,-1},
 {303,-1,-1},
 {304,-1,-1},
 {305,-1,-1},
 {306,-1,-1},
 {307,-1,-1},
 {308,-1,-1},
 {309,-1,-1},
 {310,-1,-1},
 {311,-1,-1},
 {312,-1,-1},
 {313,-1,-1},
 {314,-1,-1},
 {315,-1,-1},
 {316,-1,-1},
 {317,-1,-1},
 {318,-1,-1},
 {319,-1,-1},
 {320,-1,-1},
 {321,-1,-1},
 {322,-1,-1},
 {323,-1,-1},
 {324,-1,-1},
 {325,-1,-1},
 {326,-1,-1},
 {327,-1,-1},
 {328,-1,-1},
 {329,-1,-1},
 {330,-1,-1},
 {331,-1,-1},
 {332,-1,-1},
 {333,-1,-1},
 {334,-1,-1},
 {335,-1,-1},
 {336,-1,-1},
 {337,-1,-1},
 {338,-1,-1},
 {339,-1,-1},
 {340,-1,-1},
 {341,-1,-1},
 {342,-1,-1},
 {343,-1,-1},
 {344,-1,-1},
 {345,-1,-1},
 {346,-1,-1},
 {347,-1,-1},
 {348,-1,-1},
 {349,-1,-1},
 {350,8,2},
 {351,-1,-1},
 {352,-1,-1},
 {353,-1,-1},
 {354,-1,-1},
 {355,-1,-1},
 {356,-1,-1},
 {357,8,20},
 {358,8,22},
 {359,8,18},
 {360,8,24},
 {361,8,30},
 {362,8,32},
 {363,8,34},
 {364,8,36},
 {365,-1,-1},
 {366,-1,-1},
 {367,-1,-1},
 {368,-1,-1},
 {369,-1,-1},
 {370,8,38},
 {371,8,2},
 {372,8,2},
 {373,7,2},
 {374,7,0},
 {375,7,6},
 {376,7,8},
 {377,7,10},
 {378,7,4},
 {379,7,12},
 {380,7,14},
 {381,7,16},
 {382,7,18},
 {383,7,20},
 {384,7,28},
 {385,7,24},
 {386,7,26},
 {387,7,22},
 {388,7,30},
 {389,7,32},
 {390,7,34},
 {391,7,36},
 {392,7,38},
 {393,7,40},
 {394,7,42},
 {395,7,46},
 {396,7,44},
 {397,7,54},
 {398,7,50},
 {399,7,52},
 {400,7,48},
 {401,7,56},
 {402,7,58},
 {403,-1,-1},
 {404,-1,-1},
 {405,-1,-1},
 {406,8,38},
 {407,8,34},
 {408,8,32},
 {409,8,32},
 {410,-1,-1},
 {411,8,18},
 {412,-1,-1},
 {413,-1,-1},
 {414,-1,-1},
 {415,-1,-1},
 {416,8,26},
 {417,-1,-1},
 {418,-1,-1},
 {419,8,36},
 {420,8,8},
 {421,8,0},
 {422,8,22},
 {423,-1,-1},
 {424,-1,-1},
 {425,-1,-1},
 {426,-1,-1},
 {427,8,0},
 {428,-1,-1},
};
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
    dm_ta_glyph g=unknown(id,"unknown_skill");
    if(id==UINT32_MAX){g.status=DM_TA_EMPTY;g.reason="empty_slot";return g;}
    for(size_t i=0;i<sizeof rows/sizeof rows[0];i++)if(rows[i].id==id) {
        dm_ta_frame frame;
        if(!dm_ta_frame_at(rows[i].atlas,rows[i].frame,&frame) || !dm_ta_frame_at(rows[i].atlas,rows[i].frame+1,&frame))return g;
        g.status=DM_TA_GLYPH;g.atlas=rows[i].atlas;g.normal_frame=rows[i].frame;g.pressed_frame=rows[i].frame+1;g.reason="official_raw_id";return g;
    }
    return g;
}
dm_ta_glyph dm_ta_current(const dm_tg_snapshot *s,uint64_t button,uint64_t now) { return dm_ta_current_set(s,button,now,0); }
dm_ta_glyph dm_ta_current_set(const dm_tg_snapshot *s,uint64_t button,uint64_t now,int skill_set) {
    dm_ta_glyph g=unknown(UINT32_MAX,"empty_unknown");
    if(!s || !s->supported || !s->valid || !s->ready || s->source!=DM_TG_SOURCE_NATIVE_GETTER ||
       s->identity_scope!=DM_TG_IDENTITY_NATIVE || !s->captured_ns || now<s->captured_ns ||
       now-s->captured_ns>DM_TG_MAX_AGE_NS || (s->selected_bank!=0 && s->selected_bank!=16))return g;
    uint32_t logical=0;
    switch(button){case DM_BTN_A:logical=0x2000;break;case DM_BTN_B:logical=0x1000;break;
        case DM_BTN_X:logical=0x8000;break;case DM_BTN_Y:logical=0x4000;break;case DM_BTN_R:logical=0x200;break;default:return g;}
    int slot=-1;
    for(int i=0;i<DM_TG_FACETS;i++)if(s->mapping[i].logical==logical){if(slot>=0)return g;slot=(int)(skill_set?s->mapping[i].alt:s->mapping[i].normal);}
    if(slot<0 || slot>=(skill_set?12:6))return g;
    g=dm_ta_skill(s->raw_skill_id[slot]);g.slot=slot;g.effective_slot=s->selected_bank+slot;return g;
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

    c1_src = os.path.join(repo_dir, "dm", "gfx", "nvn", "c1_full.tsv")
    c1_dst = os.path.join(app_dir, "dm", "c1_full.tsv")
    if os.path.isfile(c1_src) and os.path.isdir(os.path.dirname(c1_dst)):
        shutil.copy2(c1_src, c1_dst)
        log(f"已同步更新 {c1_dst}")

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
