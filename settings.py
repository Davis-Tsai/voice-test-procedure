"""使用者設定(記在程式資料夾的 settings.json,已被 .gitignore 忽略)。"""
import json
from pathlib import Path

PATH = Path("settings.json")
DEFAULTS = {
    "mode": "auto",       # auto 常開監聽 | tap 點擊監聽
    "intro": True,        # 第一次按「開始」時念一次使用說明
    "read_hits": True,    # 語音查詢找到 1~3 項時把項目念出來
    "speed": "normal",    # 朗讀速度:slow 慢 | normal 正常 | fast 快
    "numbered": True,     # 朗讀與畫面是否自動加上步驟編號(關掉就適合拿來念文章)
    "excel": "",          # 上次在視窗裡選的對照表檔案(空白 = 用預設的 procedures.xlsx)
}


class Settings:
    def __init__(self, path=PATH):
        self.path = Path(path)
        self.data = dict(DEFAULTS)
        try:
            self.data.update({k: v for k, v in json.loads(self.path.read_text(encoding="utf-8")).items() if k in DEFAULTS})
        except (OSError, ValueError):
            pass

    def __getattr__(self, name):
        if name in DEFAULTS:
            return self.data[name]
        raise AttributeError(name)

    def set(self, **kw):
        self.data.update(kw)
        try:
            self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass
