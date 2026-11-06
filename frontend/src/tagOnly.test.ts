import { describe, expect, it } from 'vitest'
import { descIsOnlyTags } from './tagOnly'

describe('descIsOnlyTags', () => {
  it('纯话题标签判为只有标签', () => {
    expect(descIsOnlyTags('#翡翠 #手镯 #翡翠手镯')).toBe(true)
  })

  it('标签之间夹杂标点/空白/换行也判为只有标签', () => {
    expect(descIsOnlyTags('#翡翠，\n#手镯！  #好物')).toBe(true)
  })

  it('标签后面还有一句正常文案则不算', () => {
    expect(descIsOnlyTags('#翡翠 这条手镯水头很好')).toBe(false)
  })

  it('只有正文没有标签则不算', () => {
    expect(descIsOnlyTags('天然冰种A货翡翠')).toBe(false)
  })

  it('空正文不判为只有标签（那是没抓到，不是纯标签）', () => {
    expect(descIsOnlyTags('')).toBe(false)
    expect(descIsOnlyTags('   ')).toBe(false)
    expect(descIsOnlyTags(null)).toBe(false)
  })
})
