"""大字顯示視窗(永遠置頂)。語音流程在背景執行緒跑,視窗只負責顯示。"""
import queue
import tkinter as tk

FONT = "Microsoft JhengHei UI"
COLORS = {"listen": "#1a7f37", "think": "#b36b00", "speak": "#0b57d0", "error": "#c5221f", "idle": "#555555"}
HINT = "說出項目名稱或編號  ·  「重複」重新朗讀  ·  「結束」離開"


class NullUI:
    """不開視窗(--no-gui)時使用:所有顯示動作都不做事。"""

    def status(self, text, kind="idle"): pass
    def heard(self, text): pass
    def show(self, proc): pass
    def highlight(self, step): pass
    def close(self): pass


class WindowUI(NullUI):
    def __init__(self):
        self.q = queue.Queue()
        self.steps_count = 0
        root = self.root = tk.Tk()
        root.title("語音測試步驟")
        root.attributes("-topmost", True)  # 永遠在其他視窗上面
        width, height = 760, 620
        root.geometry(f"{width}x{height}+{max(0, root.winfo_screenwidth() - width - 20)}+40")
        root.minsize(480, 360)
        root.protocol("WM_DELETE_WINDOW", root.destroy)

        pad = {"padx": 16, "anchor": "w", "fill": "x"}
        self.status_lbl = tk.Label(root, text="啟動中…", font=(FONT, 22, "bold"), fg=COLORS["idle"], anchor="w")
        self.status_lbl.pack(pady=(12, 0), **pad)
        self.heard_lbl = tk.Label(root, text="", font=(FONT, 14), fg="#666666", anchor="w", justify="left")
        self.heard_lbl.pack(**pad)
        self.title_lbl = tk.Label(root, text="", font=(FONT, 34, "bold"), anchor="w", justify="left")
        self.title_lbl.pack(pady=(8, 4), **pad)
        tk.Label(root, text=HINT, font=(FONT, 11), fg="#888888", anchor="w").pack(side="bottom", pady=(0, 8), **pad)

        self.steps = tk.Text(root, font=(FONT, 24), wrap="word", relief="flat", state="disabled",
                             cursor="arrow", spacing1=6, spacing3=10, padx=8)
        self.steps.pack(fill="both", expand=True, padx=16, pady=(0, 8))
        self.steps.tag_configure("cur", background="#ffe08a", font=(FONT, 24, "bold"))

        root.bind("<Configure>", self._on_resize)
        root.after(50, self._poll)

    # --- 以下方法可從任何執行緒呼叫,實際更新交給主執行緒 ---
    def status(self, text, kind="idle"):
        self.q.put(lambda: self.status_lbl.config(text=text, fg=COLORS.get(kind, COLORS["idle"])))

    def heard(self, text):
        self.q.put(lambda: self.heard_lbl.config(text=text))

    def show(self, proc):
        def do():
            self.title_lbl.config(text=f"{proc.id}  {proc.name}")
            self.steps_count = len(proc.steps)
            self.steps.config(state="normal")
            self.steps.delete("1.0", "end")
            self.steps.insert("1.0", "\n".join(f"{i}. {s}" for i, s in enumerate(proc.steps, 1)))
            self.steps.config(state="disabled")
        self.q.put(do)

    def highlight(self, step):
        """step: 第幾步(從 1 起);不是步驟(開頭、結尾)就清掉標示。"""
        def do():
            self.steps.tag_remove("cur", "1.0", "end")
            if 1 <= step <= self.steps_count:
                self.steps.tag_add("cur", f"{step}.0", f"{step}.end")
                self.steps.see(f"{step}.0")
        self.q.put(do)

    def close(self):
        self.q.put(self.root.destroy)

    def mainloop(self):
        self.root.mainloop()

    # --- 內部 ---
    def _poll(self):
        try:
            while True:
                self.q.get_nowait()()
        except queue.Empty:
            pass
        self.root.after(50, self._poll)

    def _on_resize(self, event):
        if event.widget is self.root:
            wrap = max(200, event.width - 32)
            self.heard_lbl.config(wraplength=wrap)
            self.title_lbl.config(wraplength=wrap)
