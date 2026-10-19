# PDF 中文字体目录

把中文字体文件放在这里，导出 PDF 时会优先使用，避免依赖服务器系统字体。

## 推荐做法

从 Windows 系统复制一份黑体：

```powershell
copy C:\Windows\Fonts\simhei.ttf backend\fonts\simhei.ttf
```

或宋体：

```powershell
copy C:\Windows\Fonts\simsun.ttc backend\fonts\simsun.ttc
```

Linux 服务器可用思源黑体：

```bash
cp /usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc backend/fonts/
```

## 字体查找顺序

1. `backend/fonts/simhei.ttf`
2. `backend/fonts/simsun.ttc`
3. `C:/Windows/Fonts/simhei.ttf`
4. `C:/Windows/Fonts/msyh.ttc`
5. `/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc`
6. `/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc`

找不到任何中文字体时，导出 PDF 会抛错并提示放置字体文件。

> 注意：字体文件通常有版权限制，不建议提交到公开仓库。本目录下的 `*.ttf` / `*.ttc` 已在 `.gitignore` 中排除。
