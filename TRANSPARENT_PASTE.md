# 透明粘贴实现说明

本文梳理应用里“复制并粘贴透明图”的关键实现、调用路径与排查要点，方便后续维护或在新增功能时复用。

## 目标与原则
- **全程保留 Alpha**：底图、绘制缓冲、导出、剪贴板都用 ARGB32/PNG，不落到 RGB/JPEG。
- **兼容性优先**：同时写入剪贴板的 `image/png` 字节和一个临时 PNG 文件 URL，兼容 PPT/OneNote/笔记类软件的读取差异。
- **可视保持透明**：导出时创建透明画布，文本背景可一键设为全透明（UI 中“透明”按钮）。

## 核心管线（关键代码）
- 底图格式：`screenshot_tool.py:1857-1864`  
  - 启动时将传入的 `QPixmap` 转成 `QImage.Format_ARGB32_Premultiplied`，确保后续绘制留存 Alpha。
- 透明画布导出：`screenshot_tool.py:2986-3043`  
  - `export_pixmap()` 先 `annotated.fill(Qt.transparent)`，再依次绘制底图、矩形、文本、序号标记，返回保持透明背景的 `QPixmap`。
- 文本背景透明入口：`screenshot_tool.py:4288-4357`  
  - 文本面板的“透明”按钮会把背景色设为 `QColor(0, 0, 0, 0)`，避免文字底色挡住透明。
- 剪贴板写入：`screenshot_tool.py:3776-3794`  
  - 先 `flatten_all_annotations()`，导出 ARGB32 图，保存临时 PNG，再把 PNG 字节写到 `QMimeData` 的 `image/png`，并附上临时文件 URL，最后 `QApplication.clipboard().setMimeData(mime)`。
- 其他复制场景：`screenshot_tool.py:5249-5253`  
  - 快速重复截取后调用 `setPixmap`。如需绝对透明兼容，可参照上面的 MIME 写法调整。

## 最小可复用片段（透明粘贴）
```python
pix = canvas.export_pixmap()  # 已含透明背景
image = pix.toImage().convertToFormat(QImage.Format_ARGB32)
tmp_path = os.path.join(tempfile.gettempdir(), "snapshot_clipboard.png")
image.save(tmp_path, "PNG")  # 兼容只认文件路径的应用

buf = QBuffer()
buf.open(QIODevice.WriteOnly)
image.save(buf, "PNG")
png_bytes = bytes(buf.data())
buf.close()

mime = QMimeData()
mime.setData("image/png", png_bytes)            # 核心：带 Alpha 的 PNG 字节
mime.setUrls([QUrl.fromLocalFile(tmp_path)])    # 兼容兜底
QApplication.clipboard().setMimeData(mime)
```

## 使用与验证
- UI 操作：在标注界面点“复制”或快捷键，粘贴到 PPT/OneNote/飞书文档，背景应为透明（或棋盘格显示）。
- 若粘贴后出现白/黑底，优先检查：
  - 导出是否经过 `Format_ARGB32`，是否被转成 JPEG。
  - 是否漏掉 `annotated.fill(Qt.transparent)`。
  - 文本/矩形背景 Alpha 是否为 0（用“透明”按钮快速设定）。
  - 剪贴板里是否有 `image/png` MIME 数据（只 setPixmap 时某些应用会退化为 BMP）。

## 常见坑与排查
- **格式跌落**：任何一步转换为 `Format_RGB32` 或保存为 JPEG 都会丢透明。
- **画布默认底色**：新建 `QPixmap` 若不 fill 透明，会出现黑/白背景。
- **仅 setPixmap**：部分应用把剪贴板 Pixmap 当 BMP 读，透明会失效；确保同时写 `image/png`。
- **背景块**：文本/矩形背景默认有填充色，记得设为全透明或调低 Alpha。

## 覆盖面检查（确保透明不被破坏）
- 输入阶段：启动时强制底图转 ARGB32，默认文本背景 `transparent`，默认矩形填充 `#00000000`，保证初始链路有 Alpha。
- 绘制/编辑阶段：
  - 清除选区：`clear_selection_pixels()` 用 `CompositionMode_Clear` 将选区打成透明洞，而不是涂白。
  - 填充选区：`fill_selection_pixels()` 仍在 ARGB32 上用 SourceOver，允许半透明色。
  - 棋盘格画刷 `_checkerboard_brush()` 仅用于预览透明区域，不写入输出。
  - 撤销堆栈 `_push_image_state()` 保存的也是 ARGB32 Pixmap。
- 输出阶段：
  - 保存：`save_annotated_image()` 始终写 PNG，保留 Alpha。
  - 复制：主路径使用 `setMimeData` 携带 `image/png`；重复截屏/直接截屏路径当前用 `setPixmap`（如果某些应用仍丢透明，可按“最小可复用片段”改成 MIME 写法）。
- 源素材限制：系统屏幕截图本身没有透明通道（桌面背景会被采集进来）。只有导入自带 Alpha 的图片或在画布上挖空/透明绘制，才有实际透明区域可粘贴。

## 兼容性补充 / 可选改造
- 若希望“重复截屏”或“直接截屏后即复制”也完全沿用透明 MIME 方案，可在 `screenshot_tool.py:5046-5060`、`5249-5253` 处改用上文的剪贴板片段。
- 若需要对接 Web/接口，直接复用导出得到的 PNG 字节或 base64；勿转 JPEG/WEBP。
- 遇到只认文件路径的老软件，可保留临时 PNG URL，但尽量缩短文件名固定路径（当前 `snapshot_clipboard.png`），避免被杀毒误判锁定时的写入失败。

## 二次开发指引
- 新增导出/分享功能时，复用上方“最小可复用片段”确保透明链路。
- 若要对接 Web/REST，上游保持 PNG（含 Alpha），下游用 `image/png` MIME 或 base64 PNG。
- 性能优化时，避免改成 JPEG/WEBP 导出，否则透明丢失。
