"""麥克風辨識(STT)與朗讀(TTS)。有網路用線上服務,沒網路自動改用離線方案。"""
import asyncio
import collections
import os
import queue
import re
import socket
import time

import numpy as np
import sounddevice as sd

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

_online_cache = {}


def is_online(host, port=443, timeout=1.5):
    """快速檢查能不能連到某個服務,結果快取 10 秒。"""
    now = time.time()
    if host in _online_cache and now - _online_cache[host][0] < 10:
        return _online_cache[host][1]
    try:
        socket.create_connection((host, port), timeout).close()
        ok = True
    except OSError:
        ok = False
    _online_cache[host] = (now, ok)
    return ok


def forget_network():
    """清掉網路狀態的快取,下次檢查時重新連線測試。"""
    _online_cache.clear()


# ───────────────────────── 朗讀 ─────────────────────────

# 連續的英文字/縮寫/型號(可含數字、連字號,以及中間的空白,例如 "USB 3"、"T C 0 2 3")
_EN_RUN = re.compile(
    r"[A-Za-z][A-Za-z0-9'_/\-]*(?:\.[A-Za-z0-9]+)*"
    r"(?: +[A-Za-z0-9][A-Za-z0-9'_/\-]*(?:\.[A-Za-z0-9]+)*)*"
)


# 小數、IP、版本號(8.8.8.8、3.3):用中文語音念,小數點念成「點」
_DECIMAL = re.compile(r"(?<![A-Za-z0-9.])\d+(?:\.\d+)+")


def _split_words(text):
    out, pos = [], 0
    for m in _EN_RUN.finditer(text):
        out.append(("zh", text[pos:m.start()]))
        out.append(("en", m.group()))
        pos = m.end()
    out.append(("zh", text[pos:]))
    return out


# 數字後面的單位用中文念(1 kHz → 1千赫茲),英文語音會把 kHz 念成一個個字母
_UNITS = {
    "kHz": "千赫茲", "MHz": "兆赫茲", "GHz": "吉赫茲", "Hz": "赫茲",
    "mV": "毫伏特", "V": "伏特", "mA": "毫安培", "A": "安培", "W": "瓦特",
    "dB": "分貝", "Ω": "歐姆", "ms": "毫秒",
}
_UNIT_RE = re.compile(
    r"(?<![A-Za-z])(\d+(?:\.\d+)?)\s*(" + "|".join(sorted(map(re.escape, _UNITS), key=len, reverse=True)) + r")(?![A-Za-z])"
)


# 一個片段開頭的標點(例如英文編號後面接的「。」)不能交給語音念,否則會被念成「句號」;
# 把它換成停頓:句號類停久一點,逗號類停短一點。片段中間的標點語音本來就只會停頓,不用處理。
_LEAD_PUNCT = re.compile(r"^[\s。，、；：！？…,.;:!?「」『』（）()《》【】]+")
_GAP_LONG = 0.3
_GAP_SHORT = 0.15


def _gap(punct):
    if any(c in "。；：！？…;:!?." for c in punct):
        return _GAP_LONG
    if any(c in "，、," for c in punct):
        return _GAP_SHORT
    return 0.0


def segment(text):
    """把一段文字切成 [(語言, 文字, 前面要停頓幾秒)]:英文走英文語音,其餘(中文與數字)走中文語音。"""
    text = _UNIT_RE.sub(lambda m: m.group(1) + _UNITS[m.group(2)], text)
    parts, pos = [], 0
    for m in _DECIMAL.finditer(text):
        parts += _split_words(text[pos:m.start()])
        parts.append(("zh", m.group().replace(".", "點")))
        pos = m.end()
    parts += _split_words(text[pos:])

    merged = []  # 相鄰的同語言片段合併,念起來才連貫
    for lang, t in parts:
        if merged and merged[-1][0] == lang:
            merged[-1] = (lang, merged[-1][1] + (" " if lang == "en" and t.strip() else "") + t)
        else:
            merged.append((lang, t))

    out, pending = [], 0.0
    for lang, t in merged:
        m = _LEAD_PUNCT.match(t)
        lead, rest = (m.group(), t[m.end():]) if m else ("", t)
        rest = rest.strip()
        if not re.search(r"\w", rest):  # 只剩標點:變成下一個片段前面的停頓
            pending += _gap(lead + rest)
            continue
        out.append((lang, rest, pending + _gap(lead)))
        pending = 0.0
    return out


class Speaker:
    EDGE_VOICES = {"zh": "zh-TW-HsiaoChenNeural", "en": "en-US-AriaNeural"}
    EDGE_HOST = "speech.platform.bing.com"
    PAUSE_SEG = 0.05   # 同一句內不同語言片段之間的停頓(秒)
    PAUSE_PARA = 0.25  # 段落(例如每一步)之間的停頓(秒)

    # 朗讀速度等級 → 倍率(與網頁版一致:慢 / 正常 / 快)
    SPEEDS = {"slow": 1.2, "normal": 1.45, "fast": 1.7}

    def __init__(self, mode="auto", speed="normal", zh_voice=None, en_voice=None):
        self.mode = mode
        self.set_speed(speed)
        self._stop = False   # stop() 被呼叫後,正在念的內容會盡快結束
        self._out = None     # 目前正在播放的輸出串流
        self.voices = dict(self.EDGE_VOICES)
        if zh_voice:
            self.voices["zh"] = zh_voice
        if en_voice:
            self.voices["en"] = en_voice

    def _items(self, paragraphs):
        items = []  # (lang, text, 這個片段之後的停頓秒數, 第幾段)
        for n, para in enumerate(paragraphs):
            segs = segment(para)
            for i, (lang, text, gap) in enumerate(segs):
                if gap and items and items[-1][3] == n:  # 前一個片段的結尾多停一下
                    prev = items[-1]
                    items[-1] = (prev[0], prev[1], prev[2] + gap, prev[3])
                items.append((lang, text, self.PAUSE_PARA if i == len(segs) - 1 else self.PAUSE_SEG, n))
        return items

    def set_speed(self, level):
        """level: slow / normal / fast。線上語音用百分比(+45% = 1.45 倍),離線語音用每分鐘字數。"""
        m = self.SPEEDS.get(level, self.SPEEDS["normal"])
        self.rate = f"{round((m - 1) * 100):+d}%"
        self.offline_rate = int(175 * m)

    def stop(self):
        """中斷正在念的內容(可從別的執行緒呼叫)。"""
        self._stop = True
        out = self._out
        if out is not None:
            try:
                out.abort()
            except Exception:
                pass

    def speak_offline(self, paragraphs):
        """直接用 Windows 內建語音念(不需要網路),用來告知「網路沒連線」。"""
        self._stop = False
        self._speak_offline(self._items(paragraphs))

    def speak(self, paragraphs, on_start=None):
        """paragraphs: 字串清單,每個字串是一段(一段內可中英混合)。
        on_start(n): 每一段開始念的時候呼叫,n 是第幾段(從 0 起),可用來同步畫面。"""
        items = self._items(paragraphs)
        self._stop = False
        done = 0
        if self.mode in ("auto", "online") and (self.mode == "online" or is_online(self.EDGE_HOST)):
            try:
                self._speak_edge(items, on_start)
                return
            except Exception as e:  # 網路中斷、服務逾時…
                if self._stop:  # 是被 stop() 中斷的,不是真的失敗
                    return
                done = getattr(e, "played", 0)
                print(f"[線上朗讀失敗,改用離線語音:{e}]")
                if self.mode == "online":
                    raise
        self._speak_offline(items[done:], on_start)

    # --- 線上:edge-tts(微軟神經網路語音) ---
    def _speak_edge(self, items, on_start=None):
        asyncio.run(self._edge_pipeline(items, on_start))

    async def _edge_pipeline(self, items, on_start=None):
        import edge_tts
        import miniaudio

        sem = asyncio.Semaphore(6)

        async def synth(lang, text):
            async with sem:
                async def fetch():
                    buf = b""
                    comm = edge_tts.Communicate(text, self.voices[lang], rate=self.rate)
                    async for chunk in comm.stream():
                        if chunk["type"] == "audio":
                            buf += chunk["data"]
                    return buf
                buf = await asyncio.wait_for(fetch(), 15)
            if not buf:
                raise RuntimeError(f"沒有收到語音資料:{text}")
            dec = miniaudio.decode(buf, output_format=miniaudio.SampleFormat.SIGNED16,
                                   nchannels=1, sample_rate=24000)
            return self._trim(np.array(dec.samples, dtype=np.int16))

        # 全部同時開始合成,但依序播放:第一句合成好就先念,後面的在背景繼續準備
        tasks = [asyncio.ensure_future(synth(lang, text)) for lang, text, _, _ in items]
        loop = asyncio.get_running_loop()
        current = None
        try:
            # 整段用同一個輸出串流,片段之間才不會因為重開裝置而出現空隙
            with sd.OutputStream(samplerate=24000, channels=1, dtype="int16") as out:
                self._out = out
                for i, (task, (_, _, pause, para)) in enumerate(zip(tasks, items)):
                    if self._stop:
                        break
                    try:
                        audio = await task
                    except Exception as e:
                        e.played = i
                        raise
                    if self._stop:
                        break
                    if on_start and para != current:
                        current = para
                        on_start(para)
                    audio = np.concatenate([audio, np.zeros(int(24000 * pause), dtype=np.int16)])
                    await loop.run_in_executor(None, out.write, audio.reshape(-1, 1))
        finally:
            self._out = None
            for t in tasks:
                t.cancel()

    @staticmethod
    def _trim(audio, rate=24000, threshold=400, margin=0.04):
        """edge-tts 每段前後都帶有近半秒靜音,切掉才不會片段之間卡卡的。"""
        loud = np.flatnonzero(np.abs(audio) > threshold)
        if loud.size == 0:
            return audio
        m = int(rate * margin)
        return audio[max(0, loud[0] - m): loud[-1] + m]

    # --- 離線:Windows 內建 SAPI 語音(pyttsx3) ---
    def _speak_offline(self, items, on_start=None):
        import pyttsx3

        engine = pyttsx3.init()
        engine.setProperty("rate", self.offline_rate)
        chosen = self._pick_offline_voices(engine)
        current = None
        for lang, text, pause, para in items:
            if self._stop:
                break
            if on_start and para != current:
                current = para
                on_start(para)
            if chosen.get(lang):
                engine.setProperty("voice", chosen[lang])
            engine.say(text)
            engine.runAndWait()
            time.sleep(pause)
        engine.stop()

    @staticmethod
    def _pick_offline_voices(engine):
        prefs = {
            "zh": ("taiwan", "hanhan", "chinese", "huihui", "yaoyao", "zhiwei"),
            "en": ("united states", "zira", "david", "english"),
        }
        chosen = {}
        voices = engine.getProperty("voices")
        for lang, words in prefs.items():
            for w in words:
                hit = next((v for v in voices if w in v.name.lower() or w in v.id.lower()), None)
                if hit:
                    chosen[lang] = hit.id
                    break
        if "zh" not in chosen:
            print("[提醒] 這台電腦沒有安裝中文離線語音,中文會念不出來。"
                  "請到 設定 > 時間與語言 > 語言與區域 安裝「中文(繁體)」並勾選語音功能。")
        return chosen


# ───────────────────────── 辨識 ─────────────────────────

RATE = 16000
BLOCK = 480  # 30 毫秒


class NetworkError(Exception):
    """線上辨識連不上網路。"""


class Listener:
    HOST = "www.google.com"

    def __init__(self, language="zh-TW", min_threshold=250):
        self.language = language
        self.min_threshold = min_threshold
        self.noise = None
        self.on_recognizing = None  # 錄完音、開始辨識時呼叫(用來更新畫面狀態)

    @classmethod
    def is_online(cls):
        return is_online(cls.HOST)

    # --- 錄音:偵測到開始說話才錄,靜音約 0.9 秒就結束 ---
    def _record(self, cancel=None):
        """cancel: threading.Event,被設定時立刻結束並回傳 None(用來讓畫面按鈕或打字查詢打斷收音)。"""
        q = queue.Queue()
        def get():
            while True:
                if cancel is not None and cancel.is_set():
                    raise InterruptedError
                try:
                    return q.get(timeout=0.1)
                except queue.Empty:
                    continue

        with sd.InputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=BLOCK,
                            callback=lambda indata, *_: q.put(indata[:, 0].copy())):
            rms = lambda b: float(np.sqrt(np.mean(b.astype(np.float32) ** 2)))
            try:
                if self.noise is None:  # 第一次先量 0.5 秒環境音
                    self.noise = float(np.mean([rms(get()) for _ in range(16)]))
                return self._record_loop(get, rms)
            except InterruptedError:
                return None

    def _record_loop(self, get, rms):
        if True:
            pre = collections.deque(maxlen=10)
            frames, speech, silence = [], 0, 0
            while True:
                blk = get()
                level = rms(blk)
                thr = max(self.min_threshold, self.noise * 3)
                if not frames:
                    if level > thr:
                        frames, speech = list(pre) + [blk], 1
                    else:
                        pre.append(blk)
                        self.noise = 0.98 * self.noise + 0.02 * level
                    continue
                frames.append(blk)
                if level > thr:
                    speech, silence = speech + 1, 0
                else:
                    silence += 1
                if silence >= 30 or len(frames) > 500:  # 靜音 0.9 秒 或 最長約 15 秒
                    break
        return np.concatenate(frames) if speech >= 8 else None

    def listen(self, cancel=None):
        """錄音並用線上服務辨識。聽不懂(或被 cancel 取消)回傳空字串;連不上網路丟出 NetworkError。"""
        pcm = self._record(cancel)
        if pcm is None:
            return ""
        if self.on_recognizing:
            self.on_recognizing()
        t0 = time.time()
        text = self._google(pcm)
        self.last_info = f"辨識耗時 {time.time() - t0:.1f} 秒"
        return text

    def _google(self, pcm):
        import speech_recognition as sr

        rec = sr.Recognizer()
        rec.operation_timeout = 8
        try:
            return rec.recognize_google(sr.AudioData(pcm.tobytes(), RATE, 2), language=self.language)
        except sr.UnknownValueError:
            return ""
        except Exception as e:  # 連線失敗、逾時…
            raise NetworkError(str(e)) from e


class TextListener:
    """沒有麥克風時,用鍵盤輸入代替(測試用)。"""

    @staticmethod
    def is_online():
        return True

    def listen(self, cancel=None):
        return input("> ").strip()
