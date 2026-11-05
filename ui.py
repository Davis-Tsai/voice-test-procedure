"""大字顯示視窗(永遠置頂)。語音流程在背景執行緒跑,視窗負責顯示並把使用者的操作放進 events 佇列。

視窗送出的事件(都是 tuple,放在 ui.events):
  ("mic",)           按了「開始 / 停止」
  ("mode", "auto"|"tap")
  ("text", 字串)      打字查詢,或點了清單裡的項目(字串是項目編號)
  ("yes",) ("no",)    按了「是 / 不是」
  ("repeat",)        按了「重複」(重新朗讀上一個項目)
  ("procs", 項目清單)  用「選擇檔案」換了一份對照表
  ("quit",)          關閉視窗
"""
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

import procedures as pr

FONT = "Microsoft JhengHei UI"
COLORS = {"listen": "#1a7f37", "think": "#b36b00", "speak": "#0b57d0", "error": "#c5221f", "idle": "#555555"}
MODES = (("auto", "常開監聽"), ("tap", "點擊監聽"))
SPEEDS = (("slow", "慢"), ("normal", "正常"), ("fast", "快"))


class _LabelButton(tk.Label):
    """macOS 的原生按鈕不理會 bg / fg(深色按鈕上的白字會變成白底白字看不到),所以用 Label 模擬按鈕。"""

    def __init__(self, master, command=None, **kw):
        kw.pop("activebackground", None)
        kw.pop("activeforeground", None)
        kw.setdefault("relief", "solid")
        kw.setdefault("bd", 1)
        kw.setdefault("padx", 10)
        super().__init__(master, **kw)
        self.command = command
        self.bind("<ButtonPress-1>", lambda _e: self.config(relief="sunken"))
        self.bind("<ButtonRelease-1>", self._release)

    def configure(self, cnf=None, **kw):
        kw.pop("activebackground", None)  # Label 沒有這兩個選項,和 tk.Button 共用的程式碼會帶進來
        kw.pop("activeforeground", None)
        return super().configure(cnf, **kw)

    config = configure

    def _release(self, event):
        self.config(relief="solid")
        if self.command and 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height():
            self.command()


def Button(master, **kw):
    return (_LabelButton if sys.platform == "darwin" else tk.Button)(master, **kw)


def build_results(procs, query, list_all):
    """依輸入內容(或「清單」按鈕)決定要列出哪些項目。回傳 (標題, [Procedure]) 或 None(不需要清單)。"""
    q = query.strip()
    if not q and not list_all:
        return None
    if not q:
        return f"全部 {len(procs)} 項,點一下就朗讀", list(procs)
    hits = [p for _, p in pr.search(q, procs)]
    if hits:
        return f"找到 {len(hits)} 項,點一下就朗讀", hits
    near = [p for _, p in pr.rank(q, procs)]  # 沒有包含這些字的項目:列出聽起來相近的
    return (f"沒有包含「{q}」的項目,以下是相近的" if near else f"找不到「{q}」"), near


class _Base:
    headless = False

    def __init__(self, procs, settings, on_interrupt):
        self.procs = procs
        self.settings = settings
        self.on_interrupt = on_interrupt
        self.events = queue.Queue()

    def emit(self, *event, interrupt=False):
        if interrupt and self.on_interrupt:
            self.on_interrupt()  # 先讓正在念的內容停下來
        self.events.put(event)


class NullUI(_Base):
    """不開視窗(--no-gui):只用文字。在終端機輸入文字就等於打字查詢。"""
    headless = True

    def __init__(self, procs, settings, on_interrupt, on_speed=None, source=""):
        super().__init__(procs, settings, on_interrupt)
        self._query = ""
        self._list_all = False
        threading.Thread(target=self._read_stdin, daemon=True).start()

    def _read_stdin(self):
        for line in sys.stdin:
            line = line.strip()
            if line:
                self.emit("text", line, interrupt=True)

    def _print_list(self):
        res = build_results(self.procs, self._query, self._list_all)
        if res:
            header, hits = res
            print(header)
            for p in hits:
                print(f"  {p.id}  {p.name}")

    def status(self, text, kind="idle"): pass
    def heard(self, text): pass
    def show(self, proc, numbered=True): pass
    def highlight(self, step): pass
    def show_confirm(self, question): print(question)
    def hide_confirm(self): pass
    def set_button(self, text, on=False): pass
    def set_mode(self, mode): pass
    def set_query(self, text): self._query, self._list_all = text, False; self._print_list()
    def set_list_all(self, flag): self._query, self._list_all = "", flag; flag and self._print_list()
    def clear_search(self): self._query, self._list_all = "", False
    def mainloop(self): pass
    def close(self): pass


class WindowUI(_Base):
    def __init__(self, procs, settings, on_interrupt, on_speed=None, source=""):
        super().__init__(procs, settings, on_interrupt)
        self.q = queue.Queue()  # 背景執行緒要我做的事(tkinter 只能在主執行緒操作)
        self.list_all = False
        self.hits = []
        self.mode = settings.mode
        self.speed = settings.speed
        self.on_speed = on_speed

        root = self.root = tk.Tk()
        root.title("語音測試步驟")
        root.attributes("-topmost", False)  # 暫時取消永遠置頂(試用中)
        width = 780
        height = min(780, root.winfo_screenheight() - 130)  # 留空間給工作列與標題列
        root.geometry(f"{width}x{height}+{max(0, root.winfo_screenwidth() - width - 20)}+30")
        root.minsize(520, min(560, height))
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        # 底部(先排,才不會被中間的內容擠掉)
        bottom = tk.Frame(root)
        bottom.pack(side="bottom", fill="x", padx=16, pady=(4, 6))

        self.intro_var = tk.BooleanVar(value=settings.intro)
        self.hits_var = tk.BooleanVar(value=settings.read_hits)
        self.num_var = tk.BooleanVar(value=settings.numbered)
        opts = tk.Frame(bottom)
        opts.pack(side="bottom", fill="x", pady=(2, 0))
        tk.Checkbutton(opts, text="第一次按「開始」念使用說明", variable=self.intro_var, font=(FONT, 11),
                       command=lambda: settings.set(intro=self.intro_var.get())).pack(side="left")
        tk.Checkbutton(opts, text="查詢找到 1~3 項時念出來", variable=self.hits_var, font=(FONT, 11),
                       command=lambda: settings.set(read_hits=self.hits_var.get())).pack(side="left", padx=12)
        tk.Checkbutton(opts, text="步驟加上編號", variable=self.num_var, font=(FONT, 11),
                       command=lambda: settings.set(numbered=self.num_var.get())).pack(side="left")

        src = tk.Frame(bottom)
        src.pack(side="bottom", fill="x", pady=(2, 0))
        self.src_lbl = tk.Label(src, text=self._source_text(source), font=(FONT, 11), anchor="w", fg="#555555")
        self.src_lbl.pack(side="left", fill="x", expand=True)
        Button(src, text="選擇檔案", font=(FONT, 11), command=self._choose_file).pack(side="right")

        spd = tk.Frame(bottom)
        spd.pack(side="bottom", fill="x", pady=(4, 0))
        tk.Label(spd, text="朗讀速度", font=(FONT, 11)).pack(side="left", padx=(0, 8))
        self.speed_btns = {}
        for lv, label in SPEEDS:
            b = Button(spd, text=label, font=(FONT, 11), relief="solid", bd=1, width=6,
                          command=lambda lv=lv: self._set_speed(lv))
            b.pack(side="left")
            self.speed_btns[lv] = b
        self._paint_speeds()

        form = tk.Frame(bottom)
        form.pack(side="bottom", fill="x", pady=(4, 0))
        self.var = tk.StringVar()
        self.var.trace_add("write", lambda *_: self._render())
        # 輸入框、清單、步驟文字都明確指定白底黑字,不跟著系統(macOS 深色外觀下預設是白字,會看不到)
        self.entry = tk.Entry(form, textvariable=self.var, font=(FONT, 13), bg="white", fg="black", insertbackground="black")
        self.entry.pack(side="left", fill="x", expand=True, ipady=2)
        self.entry.bind("<Return>", lambda _e: self._submit())
        self.list_btn = Button(form, text="清單", font=(FONT, 11), command=self._toggle_all)
        self.list_btn.pack(side="left", padx=(8, 0))
        self.list_btn_bg = self.list_btn.cget("bg")  # 系統預設的按鈕底色(Windows 以外沒有 "SystemButtonFace" 這個顏色名)
        Button(form, text="查詢", font=(FONT, 11), command=self._submit).pack(side="left", padx=(8, 0))

        bar = tk.Frame(bottom)
        bar.pack(side="bottom", fill="x", pady=(4, 0))
        self.mic_btn = Button(bar, text="開始", font=(FONT, 16, "bold"), fg="white", bg="#0b57d0",
                                 activebackground="#0b57d0", activeforeground="white",
                                 command=lambda: self.emit("mic", interrupt=True))
        self.mic_btn.pack(side="left", fill="x", expand=True, ipady=2)
        # 右邊兩顆和「查詢」一樣大(不再跟著大按鈕拉高)
        Button(bar, text="重複", font=(FONT, 11), command=lambda: self.emit("repeat", interrupt=True)).pack(side="left", padx=(8, 0))
        Button(bar, text="停止朗讀", font=(FONT, 11), command=lambda: on_interrupt and on_interrupt()).pack(side="left", padx=(8, 0))

        modes = tk.Frame(bottom)
        modes.pack(side="bottom", fill="x")
        self.mode_btns = {}
        for m, label in MODES:
            b = Button(modes, text=label, font=(FONT, 11), relief="solid", bd=1,
                          command=lambda m=m: self.emit("mode", m, interrupt=True))
            b.pack(side="left", fill="x", expand=True)
            self.mode_btns[m] = b
        self._paint_modes()

        self.confirm = tk.Frame(root, bg="#fff4e5", highlightbackground="#f5c26b", highlightthickness=1)
        self.confirm_q = tk.Label(self.confirm, bg="#fff4e5", font=(FONT, 18, "bold"), anchor="w", justify="left")
        self.confirm_q.pack(fill="x", padx=10, pady=(8, 4))
        yn = tk.Frame(self.confirm, bg="#fff4e5")
        yn.pack(fill="x", padx=10, pady=(0, 10))
        Button(yn, text="是", font=(FONT, 22, "bold"), fg="white", bg="#1a7f37", activebackground="#1a7f37",
                  activeforeground="white", command=lambda: self.emit("yes", interrupt=True)).pack(side="left", fill="x", expand=True, ipady=6, padx=(0, 6))
        Button(yn, text="不是", font=(FONT, 22, "bold"), fg="#c5221f", command=lambda: self.emit("no", interrupt=True)).pack(
            side="left", fill="x", expand=True, ipady=6, padx=(6, 0))

        # 上方與中間
        self.status_lbl = tk.Label(root, text="尚未開始", font=(FONT, 16, "bold"), fg=COLORS["idle"], anchor="w")
        self.status_lbl.pack(fill="x", padx=16, pady=(4, 0))
        self.heard_lbl = tk.Label(root, text="", font=(FONT, 11), fg="#666666", anchor="w", justify="left")
        self.heard_lbl.pack(fill="x", padx=16)

        self.body = tk.Frame(root)
        self.body.pack(fill="both", expand=True, padx=16, pady=(4, 0))
        self.title_lbl = tk.Label(self.body, text="", font=(FONT, 22, "bold"), anchor="w", justify="left")
        self.steps = tk.Text(self.body, font=(FONT, 15), wrap="char", relief="flat", state="disabled",
                             cursor="arrow", spacing1=6, spacing3=10, padx=8, bg="white", fg="black")
        self.steps.tag_configure("cur", background="#ffe08a", font=(FONT, 15, "bold"))
        self.steps_count = 0

        self.results = tk.Frame(self.body)
        self.res_head = tk.Label(self.results, text="", font=(FONT, 13), fg="#666666", anchor="w")
        self.res_head.pack(fill="x")
        rframe = tk.Frame(self.results)
        rframe.pack(fill="both", expand=True)
        sb = tk.Scrollbar(rframe)
        sb.pack(side="right", fill="y")
        self.lb = tk.Listbox(rframe, font=(FONT, 15), activestyle="none", yscrollcommand=sb.set, selectbackground="#ffe08a",
                             selectforeground="black", relief="solid", bd=1, bg="white", fg="black")
        self.lb.pack(side="left", fill="both", expand=True)
        sb.config(command=self.lb.yview)
        self.lb.bind("<ButtonRelease-1>", self._on_pick)

        self.title_lbl.config(text="按下「開始」,然後說出測試項目名稱或編號")
        self._show_body(results=False)
        root.bind("<Configure>", self._on_resize)
        root.after(200, self._fit_wrap)
        root.after(50, self._poll)

    # ───── 背景執行緒呼叫(實際更新交給主執行緒)─────
    def _post(self, fn):
        self.q.put(fn)

    def status(self, text, kind="idle"):
        self._post(lambda: self.status_lbl.config(text=text, fg=COLORS.get(kind, COLORS["idle"])))

    def heard(self, text):
        self._post(lambda: self.heard_lbl.config(text=text))

    def show(self, proc, numbered=True):
        def do():
            steps = proc.steps_for(numbered)  # 沒有編號時每一行就是原文(拿來念文章)
            self.title_lbl.config(text=f"{proc.id}  {proc.name}")
            self.steps_count = len(steps)
            self.steps.config(state="normal")
            self.steps.delete("1.0", "end")
            self.steps.insert("1.0", "\n".join(f"{i}. {s}" if numbered else s for i, s in enumerate(steps, 1)))
            self.steps.config(state="disabled")
        self._post(do)

    def highlight(self, step):
        """step: 第幾步(從 1 起);不是步驟(開頭、結尾)就清掉標示。"""
        def do():
            self.steps.tag_remove("cur", "1.0", "end")
            if 1 <= step <= self.steps_count:
                self.steps.tag_add("cur", f"{step}.0", f"{step}.end")
                self.steps.see(f"{step}.0")
        self._post(do)

    def show_confirm(self, question):
        def do():
            self.confirm_q.config(text=question)
            self.confirm.pack(after=self.heard_lbl, fill="x", padx=16, pady=6)
        self._post(do)

    def hide_confirm(self):
        self._post(self.confirm.pack_forget)

    def set_button(self, text, on=False):
        color = "#c5221f" if on else "#0b57d0"
        self._post(lambda: self.mic_btn.config(text=text, bg=color, activebackground=color))

    def set_mode(self, mode):
        def do():
            self.mode = mode
            self._paint_modes()
        self._post(do)

    def set_query(self, text):
        def do():
            self.list_all = False
            self.var.set(text)
            self._render()
        self._post(do)

    def set_list_all(self, flag):
        def do():
            self.list_all = flag
            self.var.set("")
            self._render()
        self._post(do)

    def clear_search(self):
        def do():
            self.list_all = False
            self.var.set("")
            self._render()
        self._post(do)

    def close(self):
        self._post(self.root.destroy)

    def mainloop(self):
        self.root.mainloop()

    # ───── 主執行緒內部 ─────
    def _poll(self):
        try:
            while True:
                self.q.get_nowait()()
        except queue.Empty:
            pass
        self.root.after(50, self._poll)

    def _on_close(self):
        self.emit("quit", interrupt=True)
        self.root.destroy()

    def _on_resize(self, event):
        if event.widget is self.root:
            self._fit_wrap()

    def _fit_wrap(self):
        # 用視窗現在的實際寬度(macOS 啟動時的第一個 Configure 事件寬度只有 1,不能拿來用)
        wrap = max(240, self.root.winfo_width() - 40)
        for lbl in (self.heard_lbl, self.title_lbl, self.confirm_q):
            lbl.config(wraplength=wrap)

    def _paint_modes(self):
        for m, b in self.mode_btns.items():
            on = m == self.mode
            b.config(bg="#1c1c1e" if on else "white", fg="white" if on else "black",
                     activebackground="#1c1c1e" if on else "white", activeforeground="white" if on else "black")

    def _paint_speeds(self):
        for lv, b in self.speed_btns.items():
            on = lv == self.speed
            b.config(bg="#1c1c1e" if on else "white", fg="white" if on else "black",
                     activebackground="#1c1c1e" if on else "white", activeforeground="white" if on else "black")

    def _set_speed(self, level):
        self.speed = level
        self._paint_speeds()
        if self.on_speed:
            self.on_speed(level)

    def _show_body(self, results):
        for w in (self.title_lbl, self.steps, self.results):
            w.pack_forget()
        if results:
            self.results.pack(fill="both", expand=True)
        else:
            self.title_lbl.pack(fill="x")
            self.steps.pack(fill="both", expand=True)
        # macOS 上剛重新排版的清單/文字區不會自己重畫(一片空白),馬上處理一次待辦的繪製
        self.body.update_idletasks()

    def _render(self):
        res = build_results(self.procs, self.var.get(), self.list_all)
        self.list_btn.config(bg="#1c1c1e" if self.list_all else self.list_btn_bg,
                             fg="white" if self.list_all else "black")
        if res is None:
            self.hits = []
            self._show_body(results=False)
            return
        header, self.hits = res
        self.res_head.config(text=header)
        self.lb.delete(0, "end")
        for p in self.hits:
            alias = f"    ({'、'.join(p.aliases)})" if p.aliases else ""
            self.lb.insert("end", f" {p.id}   {p.name}{alias}")
        self._show_body(results=True)

    def _source_text(self, name):
        return f"目前資料:{name}({len(self.procs)} 項)"

    def _choose_file(self):
        """選另一份對照表(Excel):讀得進來就換掉目前的項目,並記住,下次開啟程式直接用它。"""
        opts = {"parent": self.root, "title": "選擇對照表",
                "filetypes": [("Excel 檔", "*.xlsx"), ("所有檔案", "*.*")]}
        last = Path(self.settings.excel) if self.settings.excel else None
        if last and last.parent.exists():
            opts["initialdir"] = str(last.parent)
        path = filedialog.askopenfilename(**opts)
        if not path:
            return
        try:
            procs = pr.load_procedures(path)
            if not procs:
                raise ValueError("這個檔案裡沒有任何項目")
        except Exception as e:
            messagebox.showerror("無法載入", f"{Path(path).name}\n\n{e}", parent=self.root)
            return
        self.settings.set(excel=str(path))
        self.procs = procs
        self.hits, self.list_all = [], False
        self.var.set("")
        self.steps_count = 0
        self.steps.config(state="normal")
        self.steps.delete("1.0", "end")
        self.steps.config(state="disabled")
        self.title_lbl.config(text="已載入新的對照表,按下「開始」後說出項目名稱或編號")
        self.src_lbl.config(text=self._source_text(Path(path).name))
        self._render()
        self.emit("procs", procs, interrupt=True)  # 讓語音流程也換成新的項目

    def _toggle_all(self):
        self.list_all = not self.list_all
        self._render()

    def _submit(self):
        text = self.var.get().strip()
        self.list_all = False
        self.var.set("")
        self._render()
        if text:
            self.emit("text", text, interrupt=True)

    def _on_pick(self, event):
        i = self.lb.nearest(event.y)
        box = self.lb.bbox(i) if 0 <= i < len(self.hits) else None
        if box and box[1] <= event.y < box[1] + box[3]:  # 點在最後一項下面的空白處不算
            proc = self.hits[i]
            self.list_all = False
            self.var.set("")
            self._render()
            self.emit("text", pr.pick_text(proc, self.procs), interrupt=True)  # 用編號查詢,等同你說出完整編號
