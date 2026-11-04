"""語音查詢測試步驟:說出項目名稱或編號 → 查 Excel 對照表 → 朗讀步驟。"""
import argparse
import re
import sys
import threading
import time
import traceback
from pathlib import Path

import procedures as pr
from settings import Settings
from speech_io import Listener, NetworkError, Speaker, forget_network
from ui import NullUI, WindowUI

DEFAULT_EXCEL = "procedures.xlsx"
SAMPLE_EXCEL = "procedures.sample.xlsx"
INTRO = "系統已就緒。請說出測試項目名稱或編號。說「重複」可以重新朗讀,說「結束」離開。"


def spoken_id(pid):
    """TC-023 → "T C 0 2 3",讓編號一個字一個字念清楚。"""
    spaced = re.sub(r"[A-Za-z0-9]", lambda m: m.group() + " ", pid.replace("-", " "))
    return re.sub(r" +", " ", spaced).strip()


def script(p, numbered=True):
    """組出要朗讀的內容:每個元素是一段,段與段之間會停頓。
    第 0 段是編號與名稱,第 1~N 段是各步驟(段號就是步驟號),最後一段是結尾。
    numbered=False 時不念「共 N 個步驟」「第 N 步」,每一行就照原文念(拿來念文章)。"""
    steps = p.steps_for(numbered)
    if numbered:
        paras = [f"{spoken_id(p.id)}。{p.name}。共 {len(steps)} 個步驟。"]
        paras += [f"第 {i} 步。{s}" for i, s in enumerate(steps, 1)]
    else:
        paras = [f"{spoken_id(p.id)}。{p.name}。", *steps]
    paras.append("以上。")
    return paras


def app_dir():
    """程式所在的資料夾:將來打包成 exe 時是 exe 旁邊,否則是原始碼資料夾。
    使用者自己的 procedures.xlsx 與 settings.json 都放這裡(不依賴「從哪個資料夾啟動」)。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def resource_dir():
    """打包進程式裡的唯讀資料(例如範例檔)所在的資料夾;沒打包時就是原始碼資料夾。"""
    return Path(getattr(sys, "_MEIPASS", app_dir()))


def find_excel(path, saved=""):
    """--excel 指定的檔案優先;其次是上次在視窗裡「選擇檔案」選的檔案;
    再來是程式資料夾裡的 procedures.xlsx,都沒有就用範例檔。"""
    if path:
        if not Path(path).exists():
            sys.exit(f"找不到對照表:{path}")
        return path
    if saved:
        if Path(saved).exists():
            return saved
        print(f"[上次選的對照表找不到了:{saved},改用預設]")
    own = app_dir() / DEFAULT_EXCEL
    if own.exists():
        return own
    for folder in (app_dir(), resource_dir()):
        sample = folder / SAMPLE_EXCEL
        if sample.exists():
            print(f"[目前使用範例資料。要用自己的資料,請在 {app_dir()} 建立 {DEFAULT_EXCEL}"
                  "(欄位:編號、名稱、別名、步驟),或用 --excel 指定檔案]")
            return sample
    sys.exit(f"找不到對照表 {own}。請建立它,或執行 python make_sample_excel.py 產生範例。")


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


class Voice:
    """語音主流程。畫面按鈕、打字、語音都變成事件,依序在這裡處理(跑在背景執行緒)。

    mode:  "auto" 常開監聽(按「開始」後一直聽)| "tap" 點擊監聽(按「開始」只聽一句話)
    active: 目前是否在聆聽(常開:整段期間;點擊:這一句)
    """

    def __init__(self, procs, listener, speaker, ui, settings):
        self.procs, self.listener, self.speaker, self.ui, self.settings = procs, listener, speaker, ui, settings
        self.mode = settings.mode
        self.active = False
        self.capturing = False
        self.cap_id = 0
        self.cancel = None
        self.greeted = False
        self.last = None
        self.quit = False
        listener.on_recognizing = lambda: ui.status("辨識中…", "think")

    # ───── 朗讀 ─────
    def say(self, paragraphs, on_start=None):
        self.ui.status("朗讀中", "speak")
        self.speaker.speak(paragraphs, on_start)

    def read(self, proc):
        numbered = self.settings.numbered  # 畫面與朗讀要用同一份步驟,所以只讀一次
        self.ui.clear_search()
        self.ui.show(proc, numbered)
        self.ui.highlight(0)
        print(f"→ {proc.id} {proc.name}")
        self.say(script(proc, numbered), on_start=self.ui.highlight)
        self.ui.highlight(0)

    # ───── 聆聽(背景錄音,結果以事件送回來)─────
    def start_capture(self):
        if self.capturing:
            return
        self.cap_id += 1
        cid, cancel = self.cap_id, threading.Event()
        self.cancel, self.capturing = cancel, True
        self.ui.status("聆聽中…", "listen")
        print("聆聽中…")

        def run():
            try:
                text = self.listener.listen(cancel)
            except NetworkError as e:
                if not cancel.is_set():
                    self.ui.events.put(("neterr", cid, str(e)))
                return
            except Exception as e:
                if not cancel.is_set():
                    self.ui.events.put(("error", cid, str(e)))
                return
            if not cancel.is_set():
                self.ui.events.put(("voice", cid, text))

        threading.Thread(target=run, daemon=True).start()

    def stop_capture(self):
        if self.capturing and self.cancel:
            self.cancel.set()
        self.capturing = False

    def ensure_capture(self):
        if self.active and not self.capturing and not self.quit:
            if wait_for_network(self.listener, self.speaker, self.ui):
                self.say(["網路已恢復。"])
            if self.active:
                self.start_capture()

    # ───── 畫面狀態 ─────
    def sync_ui(self):
        self.ui.set_button("停止" if self.active else "開始", on=self.active)
        self.ui.set_mode(self.mode)

    def idle(self, text=None, kind="idle"):
        self.ui.status(text or ("按「開始」後說話" if self.mode == "tap" else "已停止"), kind)

    # ───── 主迴圈 ─────
    def run(self):
        wait_for_network(self.listener, self.speaker, self.ui)
        self.sync_ui()
        self.ui.status("按「開始」後說話" if self.mode == "tap" else "尚未開始", "idle")
        if self.ui.headless:  # 文字模式沒有按鈕:一開始就開始聽,終端機輸入的文字就是打字查詢
            self.mode, self.active, self.greeted = "auto", True, True
            print("文字模式:直接輸入項目名稱、編號或「查詢 關鍵字」;Ctrl+C 結束。")
        while not self.quit:
            self.ensure_capture()
            self.dispatch(self.ui.events.get())

    def dispatch(self, ev):
        kind = ev[0]
        if kind == "quit":
            self.quit = True
            self.stop_capture()
        elif kind in ("mic", "mode"):
            self.control(ev)
        elif kind == "procs":
            self.set_procs(ev[1])
        elif kind == "repeat":
            self.stop_capture()
            if self.mode == "tap":
                self.active = False
                self.sync_ui()
            self.repeat_last()
            if not self.active and not self.quit:
                self.idle()
        elif kind in ("voice", "neterr", "error"):
            if ev[1] != self.cap_id or not self.capturing:
                return  # 已經被取消或過期的結果
            self.capturing = False
            if kind == "voice":
                self.on_voice(ev[2])
            elif kind == "neterr":
                print(f"[線上辨識失敗:{ev[2]}]")
                forget_network()  # 重新檢查網路;確定斷線的話,下一圈會語音告知
                if self.listener.is_online():
                    self.ui.heard("辨識服務暫時連不上")
                    self.say(["辨識服務暫時連不上,請再說一次。"])
                    if self.mode == "tap":
                        self.active = False
                        self.sync_ui()
                        self.idle()
            else:
                print(f"[錯誤:{ev[2]}]")
                self.active = False
                self.sync_ui()
                self.idle("發生錯誤,請看終端機訊息", "error")
        elif kind == "text":
            self.stop_capture()
            if self.mode == "tap":
                self.active = False
                self.sync_ui()
            print(f"輸入:{ev[1]}")
            self.ui.heard(f"輸入:{ev[1]}")
            self.handle(ev[1])
            if not self.active and not self.quit:
                self.idle()

    def set_procs(self, procs):
        """視窗上選了另一份對照表:之後比對、查詢都用新的項目,「重複」也不會念到舊檔的內容。"""
        self.procs = procs
        self.last = None
        self.ui.heard(f"已載入新的對照表:{len(procs)} 個項目")
        print(f"已換成新的對照表:{len(procs)} 個項目")
        if not self.active:
            self.idle()

    def control(self, ev):
        """「開始 / 停止」按鈕與模式切換。"""
        if ev[0] == "mode":
            self.stop_capture()
            self.active = False
            self.mode = ev[1]
            self.settings.set(mode=self.mode)
            self.ui.heard("")
            self.sync_ui()
            self.idle("尚未開始" if self.mode == "auto" else None)
            return
        if self.active:  # 停止
            self.active = False
            self.stop_capture()
            self.sync_ui()
            self.idle()
            return
        self.active = True
        self.sync_ui()
        if self.mode == "auto" and self.settings.intro and not self.greeted:
            self.greeted = True  # 使用說明只在開啟程式後第一次念(設定裡可以關閉)
            self.say([INTRO])

    def on_voice(self, text):
        if self.mode == "tap":
            self.active = False
            self.sync_ui()
        if not text:
            if self.mode == "tap":
                self.idle("沒聽到聲音,請按「開始」再試", "error")
            return
        print(f"聽到:{text}  ({getattr(self.listener, 'last_info', '')})")
        self.ui.heard(f"聽到:{text}")
        self.handle(text)
        if not self.active and not self.quit:
            self.idle()

    # ───── 處理一句話 ─────
    def handle(self, heard):
        ranked = pr.rank(heard, self.procs)
        confident = pr.is_confident(ranked)
        cmd = None if confident else pr.command(heard)
        if cmd == "quit":
            self.say(["好的,再見。"])
            self.quit = True
            self.ui.close()
            return
        if cmd == "repeat":
            self.repeat_last()
            return
        query = None if confident else pr.parse_query(heard)
        if query:
            return self.handle_query(*query)

        chosen = None
        if confident:
            chosen = ranked[0][1]
        else:  # 不太確定:逐一詢問最接近的幾個(畫面上有「是 / 不是」按鈕,也可以用說的)
            try:
                for _, proc in ranked:
                    self.ui.show_confirm(f"請問是 {proc.id} {proc.name} 嗎?")
                    self.say([f"請問是 {spoken_id(proc.id)}。{proc.name} 嗎?請回答是或不是。"])
                    ans = self.wait_answer()
                    if isinstance(ans, tuple):  # 沒回答是/不是,而是打了新的查詢
                        self.ui.hide_confirm()
                        return self.handle(ans[1])
                    if ans is None:
                        return
                    if ans:
                        chosen = proc
                        break
            finally:
                self.ui.hide_confirm()
        if not chosen:
            self.ui.heard(f"聽到:{heard}  (找不到對應的測試項目)")
            self.say(["找不到對應的測試項目,請再說一次。"])
            return
        self.last = chosen
        self.read(chosen)

    def repeat_last(self):
        """重新朗讀上一個項目(說「重複」,或按「重複」按鈕)。"""
        if self.last:
            self.read(self.last)
        else:
            self.say(["還沒有可以重複的內容,請先說出測試項目。"])

    def wait_answer(self):
        """等「是 / 不是」:按畫面按鈕、打字,或(聆聽中)用說的。
        回傳 True / False / ("text", 字串)(打了別的查詢)/ None(取消)。"""
        misses = 0
        while not self.quit:
            self.ensure_capture()
            ev = self.ui.events.get()
            kind = ev[0]
            if kind in ("yes", "no"):
                self.stop_capture()
                if self.mode == "tap":
                    self.active = False
                    self.sync_ui()
                return kind == "yes"
            if kind == "text":
                self.stop_capture()
                a = pr.yes_no(ev[1])
                return a if a is not None else ("text", ev[1])
            if kind in ("voice", "neterr", "error"):
                if ev[1] != self.cap_id or not self.capturing:
                    continue
                self.capturing = False
                if kind != "voice":
                    continue
                if self.mode == "tap":
                    self.active = False
                    self.sync_ui()
                a = pr.yes_no(ev[2]) if ev[2] else None
                if a is not None:
                    return a
                misses += 1
                if misses >= 2:
                    return None
                continue
            if kind == "mic" and self.mode == "tap" and not self.active:
                self.active = True  # 點擊監聽:按「開始」→ 聽這次的回答
                self.sync_ui()
                continue
            if kind == "quit":
                self.quit = True
                return None
            if kind == "repeat":  # 正在問「是不是…」時按了「重複」:問題作廢,交回主迴圈處理
                self.ui.events.put(ev)
                return None
            if kind == "procs":  # 正在問「是不是…」時換了檔案:舊問題作廢
                self.set_procs(ev[1])
                return None
            if kind in ("mic", "mode"):
                self.control(ev)
                return None
        return None

    def handle_query(self, kind, keywords):
        """語音查詢:把關鍵字放進輸入框、顯示清單,並簡短回應。"""
        if kind == "close":
            self.ui.clear_search()
            self.ui.heard("已收起清單")
            return
        if kind == "all":
            self.ui.set_list_all(True)
            self.say([f"共 {len(self.procs)} 項,請看畫面。"])
            return
        if not keywords:
            self.say(["請說要查詢的關鍵字,例如:查詢 電源。"])
            return
        self.ui.set_query(keywords)  # 讓你看到查的是什麼,也可以接著用打字修改
        hits = pr.search(keywords, self.procs)
        if not hits:
            self.say([f"找不到含有「{keywords}」的項目,請換個說法。"])
        elif len(hits) <= 3 and self.settings.read_hits:
            names = ";".join(f"{spoken_id(p.id)}。{p.name}" for _, p in hits)
            self.say([f"找到 {len(hits)} 項:{names}。請說編號或名稱。"])
        else:
            self.say([f"找到 {len(hits)} 項,請看畫面。"])


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--excel", help="對照表檔案(預設 procedures.xlsx;找不到時改用範例 procedures.sample.xlsx)")
    ap.add_argument("--tts", choices=["auto", "online", "offline"], default="auto", help="朗讀:有網路用線上,否則離線")
    ap.add_argument("--threshold", type=int, default=250, help="麥克風音量門檻,太吵就調高(預設 250)")
    ap.add_argument("--in-device", help="麥克風裝置編號或名稱(可用 --devices 查看)")
    ap.add_argument("--out-device", help="播放裝置編號或名稱")
    ap.add_argument("--devices", action="store_true", help="列出音訊裝置後結束")
    ap.add_argument("--no-gui", action="store_true", help="不開大字視窗,只用文字介面(在終端機輸入文字查詢)")
    ap.add_argument("--list", action="store_true", help="列出對照表內容後結束")
    args = ap.parse_args()

    import sounddevice as sd
    if args.devices:
        print(sd.query_devices())
        return
    if args.in_device or args.out_device:
        pick = lambda v: int(v) if v and v.isdigit() else v
        sd.default.device = (pick(args.in_device), pick(args.out_device))

    settings = Settings(app_dir() / "settings.json")
    excel = find_excel(args.excel, settings.excel)
    try:
        procs = pr.load_procedures(excel)
    except Exception as e:
        if args.excel or str(excel) != settings.excel:
            raise
        # 上次選的檔案壞了或格式不對:不要讓程式打不開,改用預設的
        print(f"[上次選的對照表無法載入({e}),改用預設]")
        excel = find_excel(None, "")
        procs = pr.load_procedures(excel)
    if args.list:
        for p in procs:
            print(f"{p.id}\t{p.name}\t別名:{'、'.join(p.aliases) or '-'}\t{len(p.steps)} 步")
        return

    speaker = Speaker(args.tts, settings.speed)

    def on_speed(level):  # 視窗上選了朗讀速度:立刻生效並記住
        speaker.set_speed(level)
        settings.set(speed=level)

    listener = Listener(min_threshold=args.threshold)
    ui = (NullUI if args.no_gui else WindowUI)(procs, settings, speaker.stop, on_speed, source=Path(excel).name)
    voice = Voice(procs, listener, speaker, ui, settings)
    print(f"已載入 {len(procs)} 個測試項目。" + ("Ctrl+C 可隨時結束。" if args.no_gui else "關閉視窗即可結束。"))

    def worker():
        try:
            voice.run()
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
