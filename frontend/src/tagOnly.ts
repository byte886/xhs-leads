/**
 * 正文是否只有话题标签。
 *
 * 小红书很多笔记正文就是一堆 #话题：作者发布时勾选的标签，用于引流识别，本身没有文字内容。
 * 这类笔记可以评分选品、可以看互动数据，但没有可克隆仿写的文字。
 *
 * 判断口径：去掉所有 #话题、标点、空白之后什么都不剩 => 只有标签。
 * 注意空正文返回 false：那是「没抓到正文」，与「正文只有标签」是两件事。
 */
export function descIsOnlyTags(desc: string | null | undefined): boolean {
  const raw = (desc || '').replace(/[\r\n]+/g, ' ').trim()
  if (!raw) return false
  const rest = raw
    .replace(/#[^#\s]+/g, '')                   // 去掉 #话题
    .replace(/[，。！？、,.!?\s@【】（）()]/g, '')  // 去掉标点、空白、@
    .trim()
  return rest.length === 0
}
