"""產生範例對照表 procedures.sample.xlsx(已存在時不會覆蓋)。照這個格式換成你自己的項目,存成 procedures.xlsx 即可。"""
import sys
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font

ROWS = [
    ("TC-001", "電源開關測試", "開機測試;power on test",
     "確認電源線已接上\n按下電源鍵 3 秒\n確認 power LED 亮綠燈\n確認螢幕在 10 秒內顯示 logo"),
    ("TC-002", "USB 傳輸測試", "USB 測試;USB 讀寫測試",
     "將 USB 隨身碟插入 Type-C 埠\n開啟檔案總管確認磁碟出現\n複製 100MB 測試檔案到隨身碟\n確認複製時間少於 5 秒\n安全移除隨身碟"),
    ("TC-003", "網路連線測試", "Wi-Fi 測試;無線網路測試;network test",
     "開啟設定頁面選擇 Wi-Fi\n連接到測試用 SSID\n開啟命令提示字元輸入 ping 8.8.8.8\n確認封包遺失率為 0%"),
    ("TC-023", "喇叭音量測試", "音量測試;speaker test",
     "播放 1 kHz 測試音\n將音量調到 50%\n確認左右聲道都有聲音\n將音量調到最大並確認沒有破音"),
    ("TC-028", "按鍵功能測試", "按鈕測試;button test",
     "依序按下所有實體按鍵\n確認每個按鍵在螢幕上有對應反應\n長按重置鍵 5 秒確認裝置重新啟動"),
]


def main():
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "procedures.sample.xlsx")
    if path.exists():
        sys.exit(f"{path} 已存在,不覆蓋。")
    wb = Workbook()
    ws = wb.active
    ws.title = "procedures"
    ws.append(["編號", "名稱", "別名", "步驟"])
    for r in ROWS:
        ws.append(r)
    for c in ws[1]:
        c.font = Font(bold=True)
    for col, width in zip("ABCD", (10, 22, 36, 70)):
        ws.column_dimensions[col].width = width
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
    wb.save(path)
    print(f"已建立 {path}")


if __name__ == "__main__":
    main()
