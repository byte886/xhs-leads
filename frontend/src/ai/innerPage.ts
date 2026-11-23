/**
 * 内页真渲染：1242×1656（3:4），按图片模板的骨架 + 皮肤 + 大纲正文画成 canvas。
 *
 * 预览与「填入发布页」用的是同一张，所见即所得 —— 不画抽象色块。
 * 抽成纯模块（不碰组件状态）的原因：这段是 AiWizard 里最长的一块绘制分支，
 * 出问题要能单独立起来看；组件只负责把「当前大纲页 + 当前模板」递进来。
 */
import type { ImageTemplate, OutlinePage } from '../api'

export const INNER_W = 1242
export const INNER_H = 1656
export const SANS_STACK = '"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif'
export const SERIF_STACK = '"Songti SC","STSong","SimSun",serif'

/** 按像素宽度折行（中文逐字，遇 \n 强制断行） */
export function wrapText(ctx: CanvasRenderingContext2D, text: string, maxW: number): string[] {
  const out: string[] = []
  let line = ''
  for (const ch of Array.from(text || '')) {
    if (ch === '\n') { if (line) out.push(line); line = ''; continue }
    const next = line + ch
    if (ctx.measureText(next).width > maxW && line) { out.push(line); line = ch } else line = next
  }
  if (line) out.push(line)
  return out
}

/** 圆角矩形路径（不依赖 ctx.roundRect，老 Chromium 也能用） */
export function roundRectPath(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  const rr = Math.max(0, Math.min(r, Math.min(w, h) / 2))
  ctx.beginPath()
  ctx.moveTo(x + rr, y)
  ctx.arcTo(x + w, y, x + w, y + h, rr)
  ctx.arcTo(x + w, y + h, x, y + h, rr)
  ctx.arcTo(x, y + h, x, y, rr)
  ctx.arcTo(x, y, x + w, y, rr)
  ctx.closePath()
}

export interface InnerPageInput {
  /** 大纲里对应的那一页（缺省时用「这一页讲什么」占位） */
  page?: OutlinePage
  /** 选中的图片模板（骨架与皮肤都从这里取） */
  template?: ImageTemplate
  /** 第几张内页（0 起，决定页眉序号） */
  index: number
}

/** 把一页大纲按模板骨架画成 3:4 真图，返回 canvas（预览与落盘共用同一绘制结果） */
export function renderInnerCanvas({ page, template, index }: InnerPageInput): HTMLCanvasElement | null {
  const heading = (page?.heading || '这一页讲什么').trim()
  const copy = (page?.copy || '').trim()
  const skin = template?.skin
  const bg = skin?.bg || '#f7f2ec'
  const primary = skin?.primary || '#e22a43'
  const skeleton = template?.skeleton || 'card'
  const radius = skin?.radius ?? 8
  const centered = skin?.align === 'center'
  const font = skin?.font === 'serif' ? SERIF_STACK : SANS_STACK

  const cv = document.createElement('canvas')
  cv.width = INNER_W
  cv.height = INNER_H
  const ctx = cv.getContext('2d')
  if (!ctx) return null

  ctx.fillStyle = bg
  ctx.fillRect(0, 0, INNER_W, INNER_H)

  const pad = 108
  const innerW = INNER_W - pad * 2
  const x = centered ? INNER_W / 2 : pad
  ctx.textAlign = centered ? 'center' : 'left'
  ctx.textBaseline = 'alphabetic'

  const paint = (lines: string[], startY: number, size: number, gap: number) => {
    lines.forEach((l, i) => ctx.fillText(l, x, startY + i * (size + gap)))
    return startY + (lines.length - 1) * (size + gap)
  }
  const headingLines = (size: number) => {
    ctx.fillStyle = primary
    ctx.font = `700 ${size}px ${font}`
    return wrapText(ctx, heading, innerW).slice(0, 2)
  }
  const copyLines = (size: number, alpha: number, max: number) => {
    ctx.globalAlpha = alpha
    ctx.fillStyle = primary
    ctx.font = `400 ${size}px ${font}`
    const ls = wrapText(ctx, copy, innerW).slice(0, max)
    ctx.globalAlpha = 1
    return ls
  }

  // 页眉编号：三张内页带一致的序号，看起来是一套
  if (skin?.number !== false) {
    ctx.globalAlpha = 0.55
    ctx.fillStyle = primary
    ctx.font = `600 36px ${font}`
    ctx.fillText(`${String(index + 1).padStart(2, '0')} / 03`, x, pad)
    ctx.globalAlpha = 1
  }

  if (skeleton === 'num') {
    // 大数字：巨型序号当主视觉，标题与正文压在下面，别让下半页空着
    ctx.globalAlpha = 0.9
    ctx.fillStyle = primary
    ctx.font = `700 330px ${font}`
    ctx.fillText(String(index + 1).padStart(2, '0'), x, 560)
    ctx.globalAlpha = 1
    paint(headingLines(104), 740, 104, 26)
    paint(copyLines(54, 0.74, 5), 940, 54, 84)
  } else if (skeleton === 'list') {
    // 清单：标题在上，正文按句拆成带圆点的条目
    paint(headingLines(84), 300, 84, 24)
    const rows = (copy || '条目一。条目二。条目三。').split(/[。；;]/).filter(Boolean).slice(0, 4)
    ctx.font = `400 48px ${font}`
    rows.forEach((row, i) => {
      const y = 480 + i * 150
      ctx.globalAlpha = 0.85
      ctx.fillStyle = primary
      ctx.beginPath()
      ctx.arc(centered ? pad : pad + 12, y - 16, 12, 0, Math.PI * 2)
      ctx.fill()
      ctx.globalAlpha = 0.78
      const ls = wrapText(ctx, row.trim(), centered ? innerW - 48 : innerW - 56).slice(0, 2)
      ls.forEach((l, k) => ctx.fillText(l, centered ? INNER_W / 2 : pad + 56, y + k * 70))
      ctx.globalAlpha = 1
    })
  } else if (skeleton === 'card') {
    // 卡片：正文装进一张圆角块
    paint(headingLines(84), 280, 84, 24)
    const boxY = 420
    const boxH = INNER_H - boxY - pad - 60
    ctx.globalAlpha = 0.16
    ctx.fillStyle = primary
    roundRectPath(ctx, pad, boxY, innerW, boxH, radius * 3)
    ctx.fill()
    ctx.globalAlpha = 1
    ctx.textAlign = 'left'
    ctx.fillStyle = primary
    ctx.font = `400 54px ${font}`
    const body = wrapText(ctx, copy, innerW - 96).slice(0, 8)
    body.forEach((l, i) => ctx.fillText(l, pad + 48, boxY + 96 + i * 84))
    ctx.textAlign = centered ? 'center' : 'left'
  } else if (skeleton === 'compare') {
    // 对比：左右两栏，左标题右正文，中间一条竖线
    const colW = (innerW - 60) / 2
    ctx.globalAlpha = 0.14
    ctx.fillStyle = primary
    roundRectPath(ctx, pad, 300, colW, INNER_H - 300 - pad - 60, radius * 3)
    ctx.fill()
    roundRectPath(ctx, pad + colW + 60, 300, colW, INNER_H - 300 - pad - 60, radius * 3)
    ctx.fill()
    ctx.globalAlpha = 1
    ctx.textAlign = 'left'
    ctx.fillStyle = primary
    ctx.font = `700 68px ${font}`
    wrapText(ctx, heading, colW - 64).slice(0, 3)
      .forEach((l, i) => ctx.fillText(l, pad + 32, 400 + i * 96))
    ctx.font = `400 46px ${font}`
    ctx.globalAlpha = 0.8
    wrapText(ctx, copy, colW - 64).slice(0, 8)
      .forEach((l, i) => ctx.fillText(l, pad + colW + 92, 400 + i * 76))
    ctx.globalAlpha = 1
    ctx.textAlign = centered ? 'center' : 'left'
  } else if (skeleton === 'split') {
    // 分屏：上 40% 色块压标题（反白），下半留正文
    ctx.fillStyle = primary
    ctx.fillRect(0, 0, INNER_W, Math.round(INNER_H * 0.4))
    ctx.fillStyle = bg
    ctx.font = `700 104px ${font}`
    wrapText(ctx, heading, innerW).slice(0, 2)
      .forEach((l, i) => ctx.fillText(l, x, 250 + i * 130))
    ctx.fillStyle = primary
    paint(copyLines(54, 0.8, 6), Math.round(INNER_H * 0.4) + 160, 54, 30)
  } else {
    // quote 金句：正文当大字居中，上下引号
    ctx.fillStyle = primary
    ctx.globalAlpha = 0.25
    ctx.font = `700 220px ${font}`
    ctx.fillText('\u201c', x, 460)
    ctx.globalAlpha = 1
    ctx.font = `700 88px ${font}`
    const body = wrapText(ctx, copy || heading, innerW - 40).slice(0, 6)
    body.forEach((l, i) => ctx.fillText(l, x, 620 + i * 130))
    ctx.globalAlpha = 0.6
    ctx.font = `600 44px ${font}`
    wrapText(ctx, heading, innerW).slice(0, 2).forEach((l, i) => ctx.fillText(l, x, 1160 + i * 66))
    ctx.globalAlpha = 1
  }

  // 页脚：模板名 + 分隔线，让整套图有署名感
  if (skin?.divider !== false) {
    ctx.globalAlpha = 0.22
    ctx.strokeStyle = primary
    ctx.lineWidth = 2
    ctx.beginPath()
    ctx.moveTo(pad, INNER_H - 150)
    ctx.lineTo(INNER_W - pad, INNER_H - 150)
    ctx.stroke()
    ctx.globalAlpha = 0.55
    ctx.fillStyle = primary
    ctx.font = `500 32px ${font}`
    ctx.fillText(template?.name || '内容模板', x, INNER_H - pad)
    ctx.globalAlpha = 1
  }

  return cv
}

/** 即时预览用：canvas → dataURL（拿不到 canvas 时返回空串，界面显示空槽） */
export function innerPageDataUrl(input: InnerPageInput): string {
  const cv = renderInnerCanvas(input)
  return cv ? cv.toDataURL('image/png') : ''
}

/** canvas → PNG File（toBlob 是异步的，包一层 Promise） */
export function canvasToFile(cv: HTMLCanvasElement, idx: number): Promise<File> {
  return new Promise((resolve, reject) => {
    cv.toBlob((blob) => {
      if (!blob) { reject(new Error('第 ' + (idx + 1) + ' 张内页导出失败')); return }
      resolve(new File([blob], `inner_${idx + 1}.png`, { type: 'image/png' }))
    }, 'image/png')
  })
}
