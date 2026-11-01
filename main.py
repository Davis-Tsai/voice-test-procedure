"""語音查詢測試步驟:說出項目名稱或編號 → 查 Excel 對照表 → 朗讀步驟。"""
import argparse
import re
import sys
import threading
import time
import traceback
from pathlib import Path

import procedures as pr
from speech_io import Listener, NetworkError, Speaker, TextListener, forget_network
from ui import NullUI, WindowUI

DEFAULT_EXCEL = "procedures.xlsx"
SAMPLE_EXCEL = "procedures.sample.xlsx"


def spoken_id(pid):
    """TC-023 → "T C 0 2 3",讓編號一個字一個字念清楚。"""
    spaced = re.sub(r"[A-Za-z0-9]", lambda m: m.group() + " ", pid.replace("-", " "))
    return re.sub(r" +", " ", spaced).strip()


def script(p):
    """組出要朗讀的內容:每個元素是一段,段與段之間會停頓。
    第 0 段是編號與名稱,第 1~N 段是各步驟(段號就是步驟號),最後一段是結尾。"""
    paras = [f"{spoken_id(p.id)}。{p.name}。共 {len(p.steps)} 個步驟。"]
    paras += [f"第 {i} 步。{s}" for i, s in enumerate(p.steps, 1)]
    paras.append("以上。")
    return paras


def find_excel(path):
    """--excel 指定的檔案優先;沒指定就用 procedures.xlsx,都沒有就用範例檔。"""
    if path:
        if not Path(path).exists():
            sys.exit(f"找不到對照表:{path}")
        return path
    if Path(DEFAULT_EXCEL).exists():
        return DEFAULT_EXCEL
    if Path(SAMPLE_EXCEL).exists():
        print(f"[目前使用範例資料 {SAMPLE_EXCEL}。要用自己的資料,請建立 {DEFAULT_EXCEL}(欄位:編號、名稱、別名、步驟),或用 --excel 指定檔案]")
        return SAMPLE_EXCEL
    sys.exit(f"找不到對照表 {DEFAULT_EXCEL}。請建立它,或執行 python make_sample_excel.py 產生範例。")


def wait_for_network(listener, speaker, ui):
    """沒有網路時用語音告知,並等到連線恢復為止。回傳是否曾經斷線。"""
    if listener.is_online():
        return False
    print("[網路沒有連線,等待連線中…]")
    ui.status("沒有網路,等待連線中…", "error")
    speaker.speak_offline(["網路沒有連線,請檢查網路。連線後會自動繼續。"])
    while not listener.is_online():
        time.sleep(3)
    print("[網路已恢復]")
    return True


def run(procs, listener, speaker, ui, text_mode):
    """語音主流程。"""
    listener.on_recognizing = lambda: ui.status("辨識中…", "think")

    def say(paragraphs, on_start=None):
        ui.status("朗讀中", "speak")
        speaker.speak(paragraphs, on_start)

    def listen():
        """聆聽並辨識;沒網路時語音告知並等到連線恢復。"""
        while True:
            if wait_for_network(listener, speaker, ui):
                say(["網路已恢復。"])
            print("請輸入:" if text_mode else "聆聽中…")
            ui.status("聆聽中…", "listen")
            try:
                heard = listener.listen()
            except NetworkError as e:
                print(f"[線上辨識失敗:{e}]")
                forget_network()  # 重新檢查網路;確定斷線的話,下一圈會語音告知
                if listener.is_online():
                    ui.heard("辨識服務暫時連不上")
                    say(["辨識服務暫時連不上,請再說一次。"])
                    return ""
                continue
            if heard:
                print(f"聽到:{heard}  ({getattr(listener, 'last_info', '')})")
                ui.heard(f"聽到:{heard}")
            return heard

    def read(proc):
        ui.show(proc)
        ui.highlight(0)
        say(script(proc), on_start=ui.highlight)
        ui.highlight(0)

    wait_for_network(listener, speaker, ui)
    say(["系統已就緒。請說出測試項目名稱或編號。說「重複」可以重新朗讀,說「結束」離開。"])
    last = None
    while True:
        heard = listen()
        if not heard:
            continue

        ranked = pr.rank(heard, procs)
        cmd = None if pr.is_confident(ranked) else pr.command(heard)
        if cmd == "quit":
            say(["好的,再見。"])
            ui.close()
            return
        if cmd == "repeat":
            if last:
                read(last)
            else:
                say(["還沒有可以重複的內容,請先說出測試項目。"])
            continue

        chosen = None
        if pr.is_confident(ranked):
            chosen = ranked[0][1]
        else:  # 不太確定:逐一詢問最接近的幾個
            for score, p in ranked:
                ui.heard(f"聽到:{heard}  →  請問是 {p.id} {p.name} 嗎?")
                say([f"請問是 {spoken_id(p.id)}。{p.name} 嗎?請回答是或不是。"])
                for _ in range(2):
                    answer = pr.yes_no(listen())
                    if answer is not None:
                        break
                if answer:
                    chosen = p
                    break
        if not chosen:
            ui.heard(f"聽到:{heard}  (找不到對應的測試項目)")
            say(["找不到對應的測試項目,請再說一次。"])
            continue

        last = chosen
        print(f"→ {chosen.id} {chosen.name}")
        read(chosen)


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--excel", help="對照表檔案(預設 procedures.xlsx;找不到時改用範例 procedures.sample.xlsx)")
    ap.add_argument("--tts", choices=["auto", "online", "offline"], default="auto", help="朗讀:有網路用線上,否則離線")
    ap.add_argument("--rate", default="+0%", help="線上朗讀速度,例如 -10%% 較慢、+20%% 較快")
    ap.add_argument("--threshold", type=int, default=250, help="麥克風音量門檻,太吵就調高(預設 250)")
    ap.add_argument("--in-device", help="麥克風裝置編號或名稱(可用 --devices 查看)")
    ap.add_argument("--out-device", help="播放裝置編號或名稱")
    ap.add_argument("--devices", action="store_true", help="列出音訊裝置後結束")
    ap.add_argument("--no-gui", action="store_true", help="不開大字視窗,只用文字介面")
    ap.add_argument("--text", action="store_true", help="用鍵盤輸入代替麥克風(測試用)")
    ap.add_argument("--list", action="store_true", help="列出對照表內容後結束")
    args = ap.parse_args()

    import sounddevice as sd
    if args.devices:
        print(sd.query_devices())
        return
    if args.in_device or args.out_device:
        pick = lambda v: int(v) if v and v.isdigit() else v
        sd.default.device = (pick(args.in_device), pick(args.out_device))

    excel = find_excel(args.excel)
    procs = pr.load_procedures(excel)
    if args.list:
        for p in procs:
            print(f"{p.id}\t{p.name}\t別名:{'、'.join(p.aliases) or '-'}\t{len(p.steps)} 步")
        return

    speaker = Speaker(args.tts, rate=args.rate)
    listener = TextListener() if args.text else Listener(min_threshold=args.threshold)
    ui = NullUI() if args.no_gui else WindowUI()
    print(f"已載入 {len(procs)} 個測試項目。" + ("Ctrl+C 可隨時結束。" if args.no_gui else "關閉視窗即可結束。"))

    def worker():
        try:
            run(procs, listener, speaker, ui, args.text)
        except Exception:
            traceback.print_exc()
            ui.status("發生錯誤,請看終端機訊息", "error")

    try:
        if args.no_gui:
            worker()
        else:
            threading.Thread(target=worker, daemon=True).start()
            ui.mainloop()
    except KeyboardInterrupt:
        print("\n已結束。")


if __name__ == "__main__":
    main()
