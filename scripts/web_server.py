"""
杨永兴战法 + SEPA策略 Web 扫描服务
Flask + SSE 实时进度推送，端口 9527
"""
import json
import queue
import threading
import logging
import os
import sys
import traceback
import uuid
import glob
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, render_template, request, jsonify, Response

from scan_params import (
    ScanParams, list_presets, get_preset,
    save_custom_preset, delete_custom_preset, copy_custom_preset,
)
from combined_scanner import CombinedScanner

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

# 历史记录存储目录
_HISTORY_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "history")
logger = logging.getLogger("web_server")

app = Flask(__name__)


def _sse_payload(event, data):
    """构建 SSE 事件字符串"""
    try:
        body = json.dumps(data, ensure_ascii=False, default=str)
    except Exception:
        body = json.dumps({"error": "json encode failed"})
    return f"event: {event}\ndata: {body}\n\n"


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/presets")
def api_presets():
    return jsonify(list_presets())


@app.route("/api/preset/<name>")
def api_preset_detail(name):
    p = get_preset(name)
    if p is None:
        return jsonify({"error": f"未找到预设: {name}"}), 404
    return jsonify(p.to_dict())


# ============ 自定义策略管理 API ============

@app.route("/api/presets/custom", methods=["GET"])
def api_custom_presets_list():
    """列出所有自定义策略"""
    from scan_params import _load_custom_presets
    custom = _load_custom_presets()
    result = [{"id": k, "name": v.get("name", k), "description": v.get("description", ""),
               "params": v.get("params", {})}
              for k, v in custom.items()]
    return jsonify(result)


@app.route("/api/presets/custom", methods=["POST"])
def api_custom_presets_save():
    """保存自定义策略"""
    try:
        data = request.get_json(force=True)
    except Exception:
        return jsonify({"ok": False, "error": "无效的JSON"}), 400
    preset_id = data.get("id", "").strip()
    name = data.get("name", "").strip()
    description = data.get("description", "").strip()
    params = data.get("params", {})
    if not preset_id or not name:
        return jsonify({"ok": False, "error": "id 和 name 不能为空"}), 400
    result = save_custom_preset(preset_id, name, description, params)
    return jsonify(result)


@app.route("/api/presets/custom/<preset_id>", methods=["DELETE"])
def api_custom_presets_delete(preset_id):
    """删除自定义策略"""
    result = delete_custom_preset(preset_id)
    if result.get("ok"):
        return jsonify(result)
    return jsonify(result), 404


@app.route("/api/presets/custom/copy", methods=["POST"])
def api_custom_presets_copy():
    """复制策略（内置或自定义均可）"""
    try:
        data = request.get_json(force=True)
    except Exception:
        return jsonify({"ok": False, "error": "无效的JSON"}), 400
    source_id = data.get("source_id", "").strip()
    new_id = data.get("new_id", "").strip()
    new_name = data.get("new_name", "").strip()
    if not source_id or not new_id or not new_name:
        return jsonify({"ok": False, "error": "source_id, new_id, new_name 不能为空"}), 400
    result = copy_custom_preset(source_id, new_id, new_name)
    if result.get("ok"):
        return jsonify(result)
    return jsonify(result), 404


@app.route("/api/scan/stream")
def api_scan_stream():
    """SSE 端点：接收参数，在后台线程执行扫描，实时推送进度"""
    # 解析参数
    raw = request.args.get("params", "{}")
    try:
        d = json.loads(raw)
    except Exception:
        d = {}
    params = ScanParams.from_dict(d) if d else ScanParams()

    # 每个 SSE 连接一个独立队列
    msg_queue = queue.Queue()

    def generate(q):
        try:
            scanner = CombinedScanner()

            def on_progress(phase, step, label, before, after, stocks=None):
                q.put(("progress", {
                    "phase": phase, "step": step, "label": label,
                    "before": before, "after": after,
                    "filtered": before - after,
                    "stocks": stocks or [],
                }))

            # 先告知前端参数
            q.put(("params", params.to_dict()))

            # 在后台线程执行扫描
            scan_result = [None]  # 用列表避免 nonlocal

            def worker():
                try:
                    scan_result[0] = scanner.scan(
                        params=params,
                        progress_callback=on_progress,
                    )
                except Exception as exc:
                    logger.error(f"扫描线程异常: {exc}")
                    q.put(("error", {"message": str(exc)}))
                finally:
                    q.put(("done", None))

            t = threading.Thread(target=worker, daemon=True)
            t.start()

            # 主循环：从队列读取并 yield SSE 事件
            while True:
                try:
                    evt, data = q.get(timeout=25)
                except queue.Empty:
                    # 心跳防止超时
                    yield _sse_payload("ping", {"ts": ""})
                    continue

                if evt == "done":
                    result = scan_result[0]
                    if result and isinstance(result, dict):
                        yield _sse_payload("result", {
                            "final_candidates": result.get("final_candidates", []),
                            "yang_candidates": result.get("yang_candidates", []),
                            "filter_log": result.get("filter_log", []),
                            "total_final": result.get("total_final", 0),
                            "total_yang": result.get("total_yang", 0),
                            "scan_time": result.get("scan_time", ""),
                            "warning": result.get("warning", ""),
                        })
                    yield _sse_payload("done", {"message": "扫描完成"})
                    break

                elif evt == "error":
                    yield _sse_payload("error", data)
                    break

                else:
                    yield _sse_payload(evt, data)

        except Exception as exc:
            logger.error(f"SSE 生成器异常:\n{traceback.format_exc()}")
            yield _sse_payload("error", {"message": str(exc)})

    return Response(
        generate(msg_queue),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Access-Control-Allow-Origin": "*",
        },
    )


# ============ 历史记录 API ============

@app.route("/api/history/save", methods=["POST"])
def api_history_save():
    """保存扫描结果到历史记录"""
    try:
        data = request.get_json(force=True)
    except Exception:
        return jsonify({"error": "无效的JSON"}), 400

    record_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    data["id"] = record_id

    os.makedirs(_HISTORY_DIR, exist_ok=True)
    fpath = os.path.join(_HISTORY_DIR, f"{record_id}.json")
    try:
        with open(fpath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, default=str)
        logger.info(f"历史记录已保存: {record_id}")
        return jsonify({"ok": True, "id": record_id})
    except Exception as e:
        logger.error(f"保存历史记录失败: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/history")
def api_history_list():
    """列出所有历史记录（按时间倒序）"""
    records = []
    try:
        os.makedirs(_HISTORY_DIR, exist_ok=True)
        files = sorted(glob.glob(os.path.join(_HISTORY_DIR, "*.json")), reverse=True)
        for fpath in files[:50]:  # 最多返回最近50条
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    rec = json.load(f)
                records.append({
                    "id": rec.get("id", ""),
                    "scan_time": rec.get("scan_time", ""),
                    "total_yang": rec.get("total_yang", 0),
                    "total_final": rec.get("total_final", 0),
                    "preset": rec.get("preset", ""),
                    "warning": rec.get("warning", ""),
                })
            except Exception:
                continue
    except Exception as e:
        logger.error(f"读取历史记录失败: {e}")
    return jsonify(records)


@app.route("/api/history/<record_id>")
def api_history_detail(record_id):
    """获取单条历史记录详情"""
    fpath = os.path.join(_HISTORY_DIR, f"{record_id}.json")
    if not os.path.exists(fpath):
        return jsonify({"error": "记录不存在"}), 404
    try:
        with open(fpath, "r", encoding="utf-8") as f:
            rec = json.load(f)
        return jsonify(rec)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 9527))
    logger.info(f"启动 Web 服务: http://127.0.0.1:{port}")
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
