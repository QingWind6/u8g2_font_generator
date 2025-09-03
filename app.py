#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, io, re, shlex, uuid, tempfile, subprocess, json, threading
from werkzeug.exceptions import RequestEntityTooLarge
from flask import Flask, request, render_template, send_file, jsonify, abort
from flask_cors import CORS


# 可通过环境变量覆盖二进制路径
OTF2BDF = os.environ.get("OTF2BDF_PATH", "otf2bdf")
BDFCONV = os.environ.get("BDFCONV_PATH", "bdfconv")
OTF2BDF_ARGS = shlex.split(os.environ.get("OTF2BDF_ARGS", "-c iso10646-1"))

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})

# 支持用环境变量覆盖，默认 200MB
MAX_MB = int(os.environ.get("U8G2_MAX_UPLOAD_MB", "200"))
app.config["MAX_CONTENT_LENGTH"] = MAX_MB * 1024 * 1024

STORE_ROOT = os.path.join(tempfile.gettempdir(), "u8g2gen")
os.makedirs(STORE_ROOT, exist_ok=True)

# --- 用于存储所有任务状态的全局变量 ---
# 结构:
# {
#   "task-id-1": {"status": "running", "step": "otf2bdf", "log": [], "result": None, "error": None},
#   "task-id-2": {"status": "complete", "step": "done", "log": [], "result": {...}, "error": None},
# }
TASKS = {}

# ------- 工具函数 -------

def which(cmd):
    from shutil import which as _which
    return _which(cmd)

def run_cmd(cmd, cwd=None, env=None):
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd, env=env)
    return p.returncode, p.stdout, p.stderr

def sanitize_symbol_name(name: str) -> str:
    name = re.sub(r'[^A-Za-z0-9_]+', '_', (name or "u8g2_font_custom").strip())
    if not name: name = "u8g2_font_custom"
    if name[0].isdigit(): name = "_" + name
    return name

def parse_range_expr(expr: str):
    expr = (expr or "").strip()
    if not expr: return []
    cps = set()
    for part in [p.strip() for p in expr.split(",") if p.strip()]:
        m = re.match(r'^(0[xX][0-9A-Fa-f]+|\d+)\s*-\s*(0[xX][0-9A-Fa-f]+|\d+)$', part)
        if m:
            a = int(m.group(1), 0); b = int(m.group(2), 0)
            if a > b: a, b = b, a
            cps.update(range(a, b+1))
        else:
            if re.match(r'^(0[xX][0-9A-Fa-f]+|\d+)$', part):
                cps.add(int(part, 0))
    return sorted(cps)

def compress_ranges(ints):
    if not ints: return []
    xs = sorted(set(ints))
    res = []; s = p = xs[0]
    for v in xs[1:]:
        if v == p + 1: p = v; continue
        res.append(f"{s}-{p}" if s != p else f"{s}")
        s = p = v
    res.append(f"{s}-{p}" if s != p else f"{s}")
    return res

def make_m_arg(codepoints):
    return ",".join(compress_ranges(codepoints))

# 预设集合
PRESETS = {
    "space": lambda: [0x20],
    "digits": lambda: list(range(0x30, 0x3A)),
    "A_Z": lambda: list(range(0x41, 0x5B)),
    "a_z": lambda: list(range(0x61, 0x7B)),
    "ascii_printable": lambda: list(range(0x20, 0x7F)),
    "latin1": lambda: list(range(0xA0, 0x100)),
    "cjk_basic": lambda: list(range(0x4E00, 0x9FA6)),
    "cn_punct": lambda: [0x3002,0xFF0C,0x3001,0xFF1F,0xFF01,0xFF1A,0xFF1B,0x201C,0x201D,0x2018,0x2019,0x300A,0x300B,0x3008,0x3009,0x3010,0x3011,0x300E,0x300F,0x2026,0x2014,0xFF08,0xFF09,0x3000],
}

# ------- 页面 -------

@app.route("/")
def index():
    deps = {
        "otf2bdf": which(OTF2BDF) or "NOT FOUND",
        "bdfconv": which(BDFCONV) or "NOT FOUND",
    }
    return render_template("index.html", deps=deps)

@app.get("/api/deps")
def api_deps():
    deps = {
        "otf2bdf": which(OTF2BDF) or "NOT FOUND",
        "bdfconv": which(BDFCONV) or "NOT FOUND",
        "max_upload_mb": app.config.get("MAX_CONTENT_LENGTH", 0) // (1024*1024)
    }
    return jsonify({"ok": True, "deps": deps})

# ------- 生成接口 -------

def run_generation_task(task_id, font_path, px, symbol, m_arg, cps_len):
    workdir = os.path.dirname(font_path)
    detail = []
    def dlog(s): detail.append(s)

    dlog(f"任务ID: {task_id}")
    dlog(f"像素: {px}, 符号: {symbol}")
    dlog(f"字符数量: {cps_len}")
    if m_arg: dlog(f"-m: {m_arg[:180] + ('...' if len(m_arg)>180 else '')}")

    try:
        env = os.environ.copy()
        env.setdefault("LC_ALL", "C")
        env.setdefault("LANG", "C")

        # 更新状态：步骤1
        TASKS[task_id]["step"] = "otf2bdf"
        TASKS[task_id]["log"].append("步骤 1/2: 正在转换字体为 BDF 格式...")

        # 1) otf2bdf
        bdf_path = os.path.join(workdir, f"tmp_{px}.bdf")
        cmd1 = [OTF2BDF, "-p", str(px), *OTF2BDF_ARGS, font_path]
        dlog("$ " + " ".join(shlex.quote(x) for x in cmd1) + f" > {bdf_path}")
        rc1, out1, err1 = run_cmd(cmd1, env=env)
        looks_like_bdf = out1.lstrip().startswith(b"STARTFONT")

        if rc1 != 0 and not looks_like_bdf:
            err_txt = (err1 or b"").decode("utf-8", errors="ignore") or (out1 or b"").decode("utf-8", errors="ignore")
            snippet = "\n".join(err_txt.strip().splitlines()[:6]) or "未知错误"
            raise Exception(f"otf2bdf 执行失败: {snippet}")

        with open(bdf_path, "wb") as bf: bf.write(out1)
        if rc1 != 0: dlog("[WARN] otf2bdf 返回码非 0，但已检测到有效 BDF，继续处理。")
        else: dlog("[OK] 生成 BDF")

        # 更新状态：步骤2
        TASKS[task_id]["step"] = "bdfconv"
        TASKS[task_id]["log"].append("步骤 2/2: 正在生成 U8g2 头文件...")

        # 2) bdfconv
        header_name = f"{symbol}.h"
        header_path = os.path.join(workdir, header_name)
        cmd2 = [BDFCONV, bdf_path, "-f", "1", "-n", symbol, "-o", header_path]
        if m_arg: cmd2.extend(["-m", m_arg])
        dlog("$ " + " ".join(shlex.quote(x) for x in cmd2))
        rc2, out2, err2 = run_cmd(cmd2, env=env)
        if rc2 != 0:
            if err2: dlog(err2.decode(errors="ignore"))
            if out2: dlog(out2.decode(errors="ignore"))
            raise Exception("bdfconv 执行失败")

        if out2: dlog(out2.decode(errors="ignore").strip())
        dlog("[OK] 生成头文件")

        # 任务成功
        TASKS[task_id]["status"] = "complete"
        TASKS[task_id]["step"] = "done"
        TASKS[task_id]["log"].append(f"生成成功：已输出 {header_name}")
        TASKS[task_id]["result"] = {
            "files": {
                "header": f"/api/download/{task_id}/header",
                "bdf": f"/api/download/{task_id}/bdf",
                "log": f"/api/download/{task_id}/log",
            },
            "header_name": header_name,
        }
    except Exception as e:
        # 任务失败
        error_message = str(e)
        dlog(f"[ERROR] {error_message}")
        TASKS[task_id]["status"] = "failed"
        TASKS[task_id]["error"] = error_message
        TASKS[task_id]["log"].append(f"生成失败: {error_message}")
    finally:
        # 无论成功失败，都写入详细日志文件
        with open(os.path.join(workdir, "log.txt"), "w", encoding="utf-8", errors="ignore") as lf:
            lf.write("\n".join(detail))


@app.post("/api/generate")
def api_generate():
    f = request.files.get("fontfile")
    if not f: return jsonify({"ok": False, "log": ["缺少上传的字体文件"]}), 400

    try:
        px = int(request.form.get("pixel_size", "0").strip())
        if px <= 0: raise ValueError
    except Exception:
        return jsonify({"ok": False, "log": ["像素大小必须是正整数"]}), 400

    symbol = sanitize_symbol_name(request.form.get("symbol", ""))

    cset = set()
    for key in request.form.getlist("presets[]"):
        if key in PRESETS: cset.update(PRESETS[key]())
    if request.form.get("include_space") == "1": cset.add(0x20)
    if custom_chars := request.form.get("custom_chars", ""): cset.update([ord(ch) for ch in custom_chars])
    if custom_ranges := request.form.get("custom_ranges", ""): cset.update(parse_range_expr(custom_ranges))
    
    cps = sorted(cset)
    m_arg = make_m_arg(cps) if cps else ""

    task_id = uuid.uuid4().hex
    workdir = os.path.join(STORE_ROOT, task_id)
    os.makedirs(workdir, exist_ok=True)

    ext = os.path.splitext(f.filename or "")[1].lower()
    if ext not in (".ttf", ".otf"): return jsonify({"ok": False, "log": ["仅支持 .ttf/.otf 字体"]}), 400
    font_path = os.path.join(workdir, f"font{ext}")
    f.save(font_path)

    TASKS[task_id] = {
      "status": "running",
      "step": "init",
      "log": ["任务已创建，准备开始..."],
      "result": None,
      "error": None
    }

    thread = threading.Thread(
        target=run_generation_task,
        args=(task_id, font_path, px, symbol, m_arg, len(cps))
    )
    thread.start()

    return jsonify({"ok": True, "task_id": task_id})

@app.get("/api/status/<task_id>")
def api_status(task_id):
    task = TASKS.get(task_id)
    if not task:
        return jsonify({"ok": False, "error": "任务不存在"}), 404
    return jsonify({"ok": True, "task": task})


# ------- 下载接口 -------

@app.get("/api/download/<token>/<kind>")
def api_download(token, kind):
    workdir = os.path.join(STORE_ROOT, token)
    if not os.path.isdir(workdir):
        abort(404)
    if kind == "header":
        hs = [x for x in os.listdir(workdir) if x.lower().endswith(".h")]
        if not hs: abort(404)
        path = os.path.join(workdir, hs[0])
        return send_file(path, as_attachment=True, download_name=hs[0])
    elif kind == "bdf":
        bdfs = [x for x in os.listdir(workdir) if x.lower().endswith(".bdf")]
        if not bdfs: abort(404)
        path = os.path.join(workdir, bdfs[0])
        return send_file(path, as_attachment=True, download_name=bdfs[0])
    elif kind == "log":
        path = os.path.join(workdir, "log.txt")
        if not os.path.isfile(path): abort(404)
        return send_file(path, as_attachment=True, download_name="log.txt")
    else:
        abort(404)

@app.errorhandler(RequestEntityTooLarge)
def handle_413(e):
    try:
        if request.path.startswith("/api/"):
            return jsonify({
                "ok": False,
                "log": [f"上传文件过大：超过 {MAX_MB} MB 限制。可设置环境变量 U8G2_MAX_UPLOAD_MB 调整。"]
            }), 413
    except Exception:
        pass
    return ("File too large", 413)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)