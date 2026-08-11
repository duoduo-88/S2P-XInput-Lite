# S2P-XInput-Lite v0.7.6

## English

v0.7.6 improves profile-management reliability and makes clipped selections
easier to inspect in the settings interface.

- Fixes profile context-menu actions so **Rename** and **Delete** always target
  the profile that was right-clicked, even when another profile is active.
- Shows the complete selected value when hovering over a combobox whose text is
  visibly clipped.
- Measures the theme's actual combobox text area, so the overflow check remains
  accurate across display scaling and ttk themes.
- Preserves existing hover and mouse-wheel bindings when adding tooltips.
- Adds regression coverage for right-clicked profile targeting and combobox
  overflow detection.
- Passes all 539 automated regression tests.

## 繁體中文

v0.7.6 改善方案管理的可靠性，並讓設定介面中遭截斷的選項更容易查看。

- 修正方案右鍵選單，確保「重新命名」與「刪除」永遠套用至實際被右鍵點選的
  方案，即使目前啟用的是另一個方案。
- 當下拉選單的已選文字確實超出顯示範圍時，滑鼠停留即可查看完整內容。
- 依 ttk 佈景主題的實際文字區域判斷是否截斷，在不同顯示縮放比例與佈景主題下
  仍能正確運作。
- 新增提示時保留既有的滑鼠停留與滾輪事件綁定。
- 增加右鍵方案目標與下拉選單溢位判斷的回歸測試。
- 通過全部 539 項自動化回歸測試。
